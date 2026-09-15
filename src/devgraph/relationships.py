from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from devgraph.model.base import WorkObject, WorkStatus
from devgraph.model.work import (
    AcceptanceCriterion,
    Decision,
    Handoff,
    Initiative,
    Issue,
    Project,
    Proposal,
    Requirement,
    ReviewPacket,
    Task,
    Todo,
)
from devgraph.storage.base import EdgeRecord, GraphStorage, NodeRecord

_PARENT_RELATIONSHIP = "HAS_CHILD"
_BLOCKS_RELATIONSHIP = "BLOCKS"
_DEPENDS_ON_RELATIONSHIP = "DEPENDS_ON"
_HAS_REQUIREMENT_RELATIONSHIP = "HAS_REQUIREMENT"
_HAS_ACCEPTANCE_CRITERION_RELATIONSHIP = "HAS_ACCEPTANCE_CRITERION"
_HAS_HANDOFF_RELATIONSHIP = "HAS_HANDOFF"
_HAS_REVIEW_PACKET_RELATIONSHIP = "HAS_REVIEW_PACKET"
_ACCEPTED_BY_DECISION_RELATIONSHIP = "ACCEPTED_BY_DECISION"
_CONVERSION_DECIDED_BY_RELATIONSHIP = "CONVERSION_DECIDED_BY"
_PARENT_RULES: dict[type[Todo], type[Todo]] = {
    Project: Initiative,
    Issue: Project,
    Task: Issue,
}
_WORK_TYPES: dict[str, type[WorkObject]] = {
    "Initiative": Initiative,
    "Project": Project,
    "Issue": Issue,
    "Task": Task,
    "Proposal": Proposal,
    "Requirement": Requirement,
    "AcceptanceCriterion": AcceptanceCriterion,
    "Handoff": Handoff,
    "ReviewPacket": ReviewPacket,
    "Decision": Decision,
}


class RelationshipGraph:
    """Typed relationship helpers for v0 work-object traversal."""

    def __init__(self, storage: GraphStorage) -> None:
        self._storage = storage

    def work_page(
        self, work: Todo, relationship: str, *, after_resource=None, limit=50
    ) -> list[Todo]:
        from devgraph.model.validation import (
            CANONICAL_WORK_KINDS,
            validate_page_limit,
            validate_work_object_id,
        )

        directions = {
            "children": ("HAS_CHILD", False),
            "parent": ("HAS_CHILD", True),
            "dependencies": ("DEPENDS_ON", False),
            "dependents": ("DEPENDS_ON", True),
            "blockers": ("BLOCKS", True),
            "blocked": ("BLOCKS", False),
        }
        if relationship not in directions or (
            relationship in {"blockers", "blocked"} and work.kind != "Task"
        ):
            raise ValueError("invalid Work relationship")
        validate_page_limit(limit)
        validate_work_object_id(work.id)
        if after_resource is not None:
            if not isinstance(after_resource, str) or after_resource.count("/") != 1:
                raise ValueError("invalid relationship cursor")
            kind, node_id = after_resource.split("/")
            if kind not in CANONICAL_WORK_KINDS:
                raise ValueError("invalid relationship cursor")
            validate_work_object_id(node_id)
        edge, incoming = directions[relationship]
        nodes = self._storage.related_work_nodes(
            work.kind, work.id, edge, incoming=incoming, after_resource=after_resource, limit=limit
        )
        return [self._todo_from_node(node) for node in nodes]

    def add_work_object(self, work_object: WorkObject) -> WorkObject:
        self._storage.create_node(
            work_object.kind,
            work_object.id,
            work_object.to_node_properties(),
        )
        return work_object

    def add_parent(self, child: Todo, parent: Todo) -> EdgeRecord:
        expected_parent_type = _PARENT_RULES.get(type(child))
        if expected_parent_type is None or not isinstance(parent, expected_parent_type):
            raise ValueError(
                "invalid parentage: v0 parentage must follow Initiative → Project → Issue → Task"
            )
        return self._storage.create_edge(
            parent.kind,
            parent.id,
            _PARENT_RELATIONSHIP,
            child.kind,
            child.id,
            {"ordered": True},
        )

    def children_of(self, parent: Todo) -> list[Todo]:
        return self.work_page(parent, "children")

    def parent_of(self, child: Todo) -> Todo | None:
        parent_edges = self._edges_to(child)
        if not parent_edges:
            return None
        edge = parent_edges[0]
        return self._todo_from_node(self._require_node(edge.from_label, edge.from_id))

    def ancestor_chain(self, child: Todo) -> list[Todo]:
        ancestors: list[Todo] = []
        current = self.parent_of(child)
        while current is not None:
            ancestors.append(current)
            current = self.parent_of(current)
        return ancestors

    def add_blocker(self, blocking_task: Task, blocked_task: Task) -> EdgeRecord:
        if not isinstance(blocking_task, Task) or not isinstance(blocked_task, Task):
            raise ValueError("BLOCKS relationships must connect Task to Task")
        return self._storage.create_edge(
            blocking_task.kind,
            blocking_task.id,
            _BLOCKS_RELATIONSHIP,
            blocked_task.kind,
            blocked_task.id,
        )

    def blockers_for(self, blocked_task: Task) -> list[Task]:
        if not isinstance(blocked_task, Task):
            raise ValueError("BLOCKS relationships must connect Task to Task")
        return self.work_page(blocked_task, "blockers")

    def blocked_by(self, blocking_task: Task) -> list[Task]:
        if not isinstance(blocking_task, Task):
            raise ValueError("BLOCKS relationships must connect Task to Task")
        return [
            self._task_from_node(self._require_node(edge.to_label, edge.to_id))
            for edge in self._edges_from(blocking_task, _BLOCKS_RELATIONSHIP)
        ]

    def add_dependency(self, dependency: Todo, dependent: Todo) -> EdgeRecord:
        return self._storage.create_edge(
            dependent.kind,
            dependent.id,
            _DEPENDS_ON_RELATIONSHIP,
            dependency.kind,
            dependency.id,
        )

    def dependencies_of(self, dependent: Todo) -> list[Todo]:
        return [
            self._todo_from_node(self._require_node(edge.to_label, edge.to_id))
            for edge in self._edges_from(dependent, _DEPENDS_ON_RELATIONSHIP)
        ]

    def dependents_of(self, dependency: Todo) -> list[Todo]:
        return [
            self._todo_from_node(self._require_node(edge.from_label, edge.from_id))
            for edge in self._edges_to(dependency, _DEPENDS_ON_RELATIONSHIP)
        ]

    def add_requirement(self, work_object: Todo, requirement: Requirement) -> EdgeRecord:
        if not isinstance(work_object, Todo):
            raise ValueError("HAS_REQUIREMENT relationships must start from Todo-derived work")
        if not isinstance(requirement, Requirement):
            raise ValueError("HAS_REQUIREMENT relationships must target Requirement")
        return self._storage.create_edge(
            work_object.kind,
            work_object.id,
            _HAS_REQUIREMENT_RELATIONSHIP,
            requirement.kind,
            requirement.id,
        )

    def requirements_for(self, work_object: Todo) -> list[Requirement]:
        return [
            self._requirement_from_node(self._require_node(edge.to_label, edge.to_id))
            for edge in self._edges_from(work_object, _HAS_REQUIREMENT_RELATIONSHIP)
        ]

    def work_objects_for_requirement(self, requirement: Requirement) -> list[Todo]:
        if not isinstance(requirement, Requirement):
            raise ValueError("HAS_REQUIREMENT relationships must target Requirement")
        return [
            self._todo_from_node(self._require_node(edge.from_label, edge.from_id))
            for edge in self._edges_to(requirement, _HAS_REQUIREMENT_RELATIONSHIP)
        ]

    def add_acceptance_criterion(
        self,
        source: Todo | Requirement,
        acceptance_criterion: AcceptanceCriterion,
    ) -> EdgeRecord:
        if not isinstance(source, (Todo, Requirement)):
            raise ValueError(
                "HAS_ACCEPTANCE_CRITERION relationships must start from "
                "Todo-derived work or Requirement"
            )
        if not isinstance(acceptance_criterion, AcceptanceCriterion):
            raise ValueError(
                "HAS_ACCEPTANCE_CRITERION relationships must target AcceptanceCriterion"
            )
        return self._storage.create_edge(
            source.kind,
            source.id,
            _HAS_ACCEPTANCE_CRITERION_RELATIONSHIP,
            acceptance_criterion.kind,
            acceptance_criterion.id,
        )

    def acceptance_criteria_for(self, source: Todo | Requirement) -> list[AcceptanceCriterion]:
        return [
            self._acceptance_criterion_from_node(self._require_node(edge.to_label, edge.to_id))
            for edge in self._edges_from(source, _HAS_ACCEPTANCE_CRITERION_RELATIONSHIP)
        ]

    def objects_for_acceptance_criterion(
        self, acceptance_criterion: AcceptanceCriterion
    ) -> list[Todo | Requirement]:
        if not isinstance(acceptance_criterion, AcceptanceCriterion):
            raise ValueError(
                "HAS_ACCEPTANCE_CRITERION relationships must target AcceptanceCriterion"
            )
        return [
            self._todo_or_requirement_from_node(self._require_node(edge.from_label, edge.from_id))
            for edge in self._edges_to(acceptance_criterion, _HAS_ACCEPTANCE_CRITERION_RELATIONSHIP)
        ]

    def add_handoff(self, work_object: Todo, handoff: Handoff) -> EdgeRecord:
        if not isinstance(work_object, Todo):
            raise ValueError("HAS_HANDOFF relationships must start from Todo-derived work")
        if not isinstance(handoff, Handoff):
            raise ValueError("HAS_HANDOFF relationships must target Handoff")
        return self._storage.create_edge(
            work_object.kind,
            work_object.id,
            _HAS_HANDOFF_RELATIONSHIP,
            handoff.kind,
            handoff.id,
        )

    def handoffs_for(self, work_object: Todo) -> list[Handoff]:
        return [
            self._handoff_from_node(self._require_node(edge.to_label, edge.to_id))
            for edge in self._edges_from(work_object, _HAS_HANDOFF_RELATIONSHIP)
        ]

    def work_objects_for_handoff(self, handoff: Handoff) -> list[Todo]:
        if not isinstance(handoff, Handoff):
            raise ValueError("HAS_HANDOFF relationships must target Handoff")
        return [
            self._todo_from_node(self._require_node(edge.from_label, edge.from_id))
            for edge in self._edges_to(handoff, _HAS_HANDOFF_RELATIONSHIP)
        ]

    def add_review_packet(self, work_object: Todo, review_packet: ReviewPacket) -> EdgeRecord:
        if not isinstance(work_object, Todo):
            raise ValueError("HAS_REVIEW_PACKET relationships must start from Todo-derived work")
        if not isinstance(review_packet, ReviewPacket):
            raise ValueError("HAS_REVIEW_PACKET relationships must target ReviewPacket")
        return self._storage.create_edge(
            work_object.kind,
            work_object.id,
            _HAS_REVIEW_PACKET_RELATIONSHIP,
            review_packet.kind,
            review_packet.id,
        )

    def review_packets_for(self, work_object: Todo) -> list[ReviewPacket]:
        return [
            self._review_packet_from_node(self._require_node(edge.to_label, edge.to_id))
            for edge in self._edges_from(work_object, _HAS_REVIEW_PACKET_RELATIONSHIP)
        ]

    def work_objects_for_review_packet(self, review_packet: ReviewPacket) -> list[Todo]:
        if not isinstance(review_packet, ReviewPacket):
            raise ValueError("HAS_REVIEW_PACKET relationships must target ReviewPacket")
        return [
            self._todo_from_node(self._require_node(edge.from_label, edge.from_id))
            for edge in self._edges_to(review_packet, _HAS_REVIEW_PACKET_RELATIONSHIP)
        ]

    def acceptance_decision_for(self, proposal: Proposal) -> Decision | None:
        if not isinstance(proposal, Proposal):
            raise ValueError("ACCEPTED_BY_DECISION relationships must start from Proposal")
        edges = self._edges_from(proposal, _ACCEPTED_BY_DECISION_RELATIONSHIP)
        if not edges:
            return None
        edge = edges[0]
        return self._decision_from_node(self._require_node(edge.to_label, edge.to_id))

    def conversion_decision_for(self, issue: Issue) -> Decision | None:
        if not isinstance(issue, Issue):
            raise ValueError("CONVERSION_DECIDED_BY relationships must start from Issue")
        edges = self._edges_from(issue, _CONVERSION_DECIDED_BY_RELATIONSHIP)
        if not edges:
            return None
        edge = edges[0]
        return self._decision_from_node(self._require_node(edge.to_label, edge.to_id))

    def _edges_from(
        self, parent: WorkObject, relationship: str = _PARENT_RELATIONSHIP
    ) -> list[EdgeRecord]:
        return [
            edge
            for edge in self._storage.list_edges(relationship=relationship)
            if edge.from_label == parent.kind and edge.from_id == parent.id
        ]

    def _edges_to(
        self, child: WorkObject, relationship: str = _PARENT_RELATIONSHIP
    ) -> list[EdgeRecord]:
        return [
            edge
            for edge in self._storage.list_edges(relationship=relationship)
            if edge.to_label == child.kind and edge.to_id == child.id
        ]

    def _require_node(self, label: str, node_id: str) -> NodeRecord:
        node = self._storage.get_node(label, node_id)
        if node is None:
            raise KeyError(f"missing node {label}:{node_id}")
        return node

    @staticmethod
    def _work_object_from_node(node: NodeRecord) -> WorkObject:
        work_type = _WORK_TYPES[node.label]
        status = node.properties.get("status")
        # Reuse the dataclass defaults for timestamps while preserving persisted metadata fields.
        obj = work_type(
            id=node.id,
            title=str(node.properties.get("title", "")),
            description=str(node.properties.get("description", "")),
            created_at=datetime.fromisoformat(str(node.properties["created_at"])),
            updated_at=datetime.fromisoformat(str(node.properties["updated_at"])),
            version=int(node.properties.get("version", 1)),
            artifact_ids=tuple(str(value) for value in node.properties.get("artifact_ids", [])),
            external_link_ids=tuple(
                str(value) for value in node.properties.get("external_link_ids", [])
            ),
            priority=int(node.properties.get("priority", 0)),
        )
        if status is None:
            return obj
        return replace(obj, status=WorkStatus(status))

    @classmethod
    def _task_from_node(cls, node: NodeRecord) -> Task:
        work_object = cls._work_object_from_node(node)
        if not isinstance(work_object, Task):
            raise TypeError(f"expected Task node, got {node.label}:{node.id}")
        return work_object

    @classmethod
    def _todo_from_node(cls, node: NodeRecord) -> Todo:
        work_object = cls._work_object_from_node(node)
        if not isinstance(work_object, Todo):
            raise TypeError(f"expected Todo node, got {node.label}:{node.id}")
        return work_object

    @classmethod
    def _requirement_from_node(cls, node: NodeRecord) -> Requirement:
        work_object = cls._work_object_from_node(node)
        if not isinstance(work_object, Requirement):
            raise TypeError(f"expected Requirement node, got {node.label}:{node.id}")
        return work_object

    @classmethod
    def _acceptance_criterion_from_node(cls, node: NodeRecord) -> AcceptanceCriterion:
        work_object = cls._work_object_from_node(node)
        if not isinstance(work_object, AcceptanceCriterion):
            raise TypeError(f"expected AcceptanceCriterion node, got {node.label}:{node.id}")
        return work_object

    @classmethod
    def _handoff_from_node(cls, node: NodeRecord) -> Handoff:
        work_object = cls._work_object_from_node(node)
        if not isinstance(work_object, Handoff):
            raise TypeError(f"expected Handoff node, got {node.label}:{node.id}")
        return work_object

    @classmethod
    def _review_packet_from_node(cls, node: NodeRecord) -> ReviewPacket:
        work_object = cls._work_object_from_node(node)
        if not isinstance(work_object, ReviewPacket):
            raise TypeError(f"expected ReviewPacket node, got {node.label}:{node.id}")
        return work_object

    @classmethod
    def _decision_from_node(cls, node: NodeRecord) -> Decision:
        work_object = cls._work_object_from_node(node)
        if not isinstance(work_object, Decision):
            raise TypeError(f"expected Decision node, got {node.label}:{node.id}")
        return work_object

    @classmethod
    def _todo_or_requirement_from_node(cls, node: NodeRecord) -> Todo | Requirement:
        work_object = cls._work_object_from_node(node)
        if not isinstance(work_object, (Todo, Requirement)):
            raise TypeError(f"expected Todo or Requirement node, got {node.label}:{node.id}")
        return work_object
