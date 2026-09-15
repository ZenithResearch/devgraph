"""Canonical named mutation implementation, called only behind authorization.

The receiver owns the outer serialized transaction and receipt. This module
owns Work validation, version preconditions, and relationship semantics.
"""

from __future__ import annotations

from devgraph.model.base import WorkStatus
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.repository import (
    ContentChanges,
    InvalidStatusTransitionError,
    WorkObjectAlreadyExistsError,
    WorkObjectRepository,
    WorkObjectVersionConflictError,
)
from devgraph.model.work import Decision, Initiative, Issue, Project, Proposal, Task
from devgraph.storage.base import GraphStorage, StorageUnavailable
from devgraph.work_requests import WorkRequest

_KINDS = {model.__name__: model for model in (Proposal, Initiative, Project, Issue, Task)}


class WorkRelationshipConflict(ValueError):
    """Safe typed relationship failure."""


class NamedWorkMutations:
    def __init__(self, storage: GraphStorage) -> None:
        self.storage = storage
        self.repository = WorkObjectRepository(storage)
        self.lifecycle = ProposalLifecycle(storage)

    def execute(self, request: WorkRequest):
        # Reparse immutable bytes; caller-supplied derived fields never select execution.
        request = WorkRequest.from_json(request.canonical)
        with self.storage.work_mutation_transaction():
            return self._execute(request)

    def _current(self, kind, work_id, version):
        current = self.repository.get_by_id(kind, work_id)
        if current.version != version:
            raise WorkObjectVersionConflictError(
                kind, work_id, expected_version=version, actual_version=current.version
            )
        return current

    def _reference(self, reference):
        return self._current(reference["kind"], reference["id"], reference["expected_version"])

    @staticmethod
    def _active(work):
        if work.status == WorkStatus.ARCHIVED:
            raise InvalidStatusTransitionError("archived Work cannot be changed")

    def _bump(self, work):
        return self.repository.update(work, expected_version=work.version)

    def _execute(self, request):
        operation, payload = request.operation, request.payload
        if operation == "create":
            values = dict(payload)
            for field in ("artifact_ids", "external_link_ids"):
                values[field] = tuple(values[field])
            return self.repository.create(_KINDS[request.kind](**values))
        subject = self._current(request.kind, request.id, request.expected_version)
        if operation == "archive":
            return self.repository.archive(subject.kind, subject.id)
        self._active(subject)
        if operation == "patch":
            return self.repository.update_content(
                subject.kind,
                subject.id,
                expected_version=subject.version,
                changes=ContentChanges.from_mapping(payload),
            )
        if operation == "status":
            return self.repository.transition_status(
                subject.kind, subject.id, WorkStatus(payload["status"])
            )
        if operation == "accept":
            if self.storage.get_node("Decision", payload["decision_id"]) is not None:
                raise WorkObjectAlreadyExistsError("Decision already exists")
            return self.lifecycle.accept_proposal(
                subject.id, Decision(id=payload["decision_id"], title=payload["decision_title"])
            )
        if operation == "convert":
            decisions = [
                edge
                for edge in self._bounded_edges("ACCEPTED_BY_DECISION")
                if (edge.from_label, edge.from_id) == (subject.kind, subject.id)
            ]
            if len(decisions) != 1 or (decisions[0].to_label, decisions[0].to_id) != (
                "Decision",
                payload["decision_id"],
            ):
                raise WorkRelationshipConflict("conversion Decision precondition failed")
            if self.storage.get_node("Issue", payload["issue_id"]) is not None:
                raise WorkObjectAlreadyExistsError("Issue already exists")
            issue = self.lifecycle.convert_accepted_proposal_to_issue(
                subject.id, payload["issue_id"]
            )
            self._bump(subject)  # The source's outgoing relationships changed too.
            return issue
        if operation == "parent.set":
            return self._set_parent(subject, payload)
        return self._edge_change(subject, operation, payload)

    def _set_parent(self, child, payload):
        all_parents = self._bounded_edges("HAS_CHILD")
        parents = [
            edge for edge in all_parents if (edge.to_label, edge.to_id) == (child.kind, child.id)
        ]
        actual = {(edge.from_label, edge.from_id) for edge in parents}
        prior = payload["previous_parent"]
        expected = set() if prior is None else {(prior["kind"], prior["id"])}
        if actual != expected:
            raise WorkRelationshipConflict("parent precondition failed")
        touched = {}
        for reference in (prior, payload["parent"]):
            if reference is not None:
                parent = self._reference(reference)
                self._active(parent)
                key = (parent.kind, parent.id)
                if key in touched and touched[key].version != reference["expected_version"]:
                    raise WorkRelationshipConflict("conflicting parent versions")
                touched[key] = parent
        from devgraph.arenas import ArenaRepository

        # Direct Arena removal is part of this transaction and must be signed.
        ArenaRepository(self.storage).remove_for_parent(child, payload.get("previous_arena"))
        for edge in parents:
            self.storage.delete_edge(
                edge.from_label, edge.from_id, "HAS_CHILD", child.kind, child.id
            )
        new_parent = payload["parent"]
        if new_parent is not None:
            self.storage.create_edge(
                new_parent["kind"],
                new_parent["id"],
                "HAS_CHILD",
                child.kind,
                child.id,
                {"ordered": True},
            )
        for parent in touched.values():
            self._bump(parent)
        return self._bump(child)

    def _edge_change(self, subject, operation, payload):
        target = self._reference(payload["target"])
        self._active(target)
        relationship = "DEPENDS_ON" if operation.startswith("dependency.") else "BLOCKS"
        # dependency: subject depends on target; blocker: subject blocks target.
        source_key, target_key = (subject.kind, subject.id), (target.kind, target.id)
        if operation.endswith(".add"):
            edges = self._bounded_edges(relationship)
            adjacency = {}
            for edge in edges:
                adjacency.setdefault((edge.from_label, edge.from_id), set()).add(
                    (edge.to_label, edge.to_id)
                )
            pending, visited = [target_key], set()
            while pending:
                node = pending.pop()
                if node == source_key:
                    raise WorkRelationshipConflict("relationship would create a cycle")
                if node not in visited:
                    visited.add(node)
                    pending.extend(adjacency.get(node, ()))
            self.storage.create_edge(subject.kind, subject.id, relationship, target.kind, target.id)
        else:
            self.storage.delete_edge(subject.kind, subject.id, relationship, target.kind, target.id)
        self._bump(target)
        return self._bump(subject)

    def _bounded_edges(self, relationship):
        edges = self.storage.list_edges(relationship, limit=10_001)
        if len(edges) > 10_000:
            raise StorageUnavailable("relationship validation budget exceeded")
        return edges
