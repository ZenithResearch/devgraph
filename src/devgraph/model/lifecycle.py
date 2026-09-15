from __future__ import annotations

from datetime import datetime

from devgraph.model.base import WorkStatus
from devgraph.model.validation import validate_canonical_work_object_properties
from devgraph.model.work import Decision, Issue, Proposal
from devgraph.storage.base import EdgeRecord, GraphStorage, NodeRecord, StorageUnavailable


class ProposalLifecycleError(ValueError):
    """Raised when a proposal lifecycle transition violates v0 invariants."""


class ProposalLifecycle:
    def __init__(self, storage: GraphStorage) -> None:
        self._storage = storage

    def create_proposal(self, proposal: Proposal) -> Proposal:
        if proposal.status == WorkStatus.ACCEPTED:
            raise ProposalLifecycleError(
                "Decision provenance is required to create accepted Proposal"
            )
        if proposal.status != WorkStatus.DRAFT:
            raise ProposalLifecycleError("Proposal must be created in draft status")
        self._storage.create_node(proposal.kind, proposal.id, proposal.to_node_properties())
        return proposal

    def get_proposal(self, proposal_id: str) -> Proposal:
        node = self._require_node("Proposal", proposal_id)
        return self._proposal_from_node(node)

    def accept_proposal(self, proposal_id: str, decision: Decision | None) -> Proposal:
        if decision is None:
            raise ProposalLifecycleError("Decision provenance is required to accept Proposal")
        proposal = self.get_proposal(proposal_id)
        if proposal.status == WorkStatus.ARCHIVED:
            raise ProposalLifecycleError("Archived Proposal cannot be accepted")
        if self._acceptance_decision_edges(proposal_id):
            raise ProposalLifecycleError(
                "Accepted Proposal already has Decision provenance"
            )
        accepted = proposal.with_status(WorkStatus.ACCEPTED)
        with self._storage.transaction():
            self._upsert_decision(decision)
            self._storage.update_node("Proposal", proposal_id, accepted.to_node_properties())
            self._storage.create_edge(
                "Proposal",
                proposal_id,
                "ACCEPTED_BY_DECISION",
                "Decision",
                decision.id,
                {"required": True},
            )
        return accepted

    def archive_proposal(self, proposal_id: str) -> Proposal:
        proposal = self.get_proposal(proposal_id)
        archived = proposal.with_status(WorkStatus.ARCHIVED)
        self._storage.update_node("Proposal", proposal_id, archived.to_node_properties())
        self._storage.archive_node("Proposal", proposal_id)
        return archived

    def convert_accepted_proposal_to_issue(self, proposal_id: str, issue_id: str) -> Issue:
        proposal = self.get_proposal(proposal_id)
        if proposal.status != WorkStatus.ACCEPTED:
            raise ProposalLifecycleError("Only accepted proposals can convert to Issue")
        decision_id = self._require_acceptance_decision_id(proposal_id)
        issue = Issue(
            id=issue_id,
            title=proposal.title,
            description=proposal.description,
            artifact_ids=proposal.artifact_ids,
            external_link_ids=proposal.external_link_ids,
            priority=proposal.priority,
        )
        with self._storage.transaction():
            self._storage.create_node(issue.kind, issue.id, issue.to_node_properties())
            self._storage.create_edge(
                "Proposal",
                proposal_id,
                "CONVERTED_TO",
                "Issue",
                issue.id,
                {"decision_id": decision_id},
            )
            self._storage.create_edge(
                "Issue",
                issue.id,
                "CONVERSION_DECIDED_BY",
                "Decision",
                decision_id,
                {"source_proposal_id": proposal_id},
            )
        return issue

    def example_active_proposal(self) -> Proposal:
        proposal = Proposal(id="example-active-proposal", title="Active Proposal")
        self.create_proposal(proposal)
        return proposal

    def example_accepted_by_decision_proposal(self) -> Proposal:
        proposal = Proposal(id="example-accepted-proposal", title="Accepted Proposal")
        decision = Decision(id="example-decision", title="Accept Proposal")
        self.create_proposal(proposal)
        return self.accept_proposal(proposal.id, decision)

    def example_archived_proposal(self) -> Proposal:
        proposal = Proposal(id="example-archived-proposal", title="Archived Proposal")
        self.create_proposal(proposal)
        return self.archive_proposal(proposal.id)

    def _require_node(self, label: str, node_id: str) -> NodeRecord:
        node = self._storage.get_node(label, node_id)
        if node is None:
            raise ProposalLifecycleError(f"missing {label}: {node_id}")
        return node

    def _upsert_decision(self, decision: Decision) -> None:
        existing = self._storage.get_node("Decision", decision.id)
        if existing is None:
            self._storage.create_node(decision.kind, decision.id, decision.to_node_properties())
            return
        self._validate_decision_node(existing, expected=decision)

    @staticmethod
    def _validate_decision_node(
        node: NodeRecord, *, expected: Decision | None = None
    ) -> None:
        try:
            if node.label != "Decision" or node.archived:
                raise ValueError("invalid Decision provenance")
            canonical = validate_canonical_work_object_properties(
                node.label,
                node.id,
                node.properties,
                archived=node.archived,
                allow_unknown=True,
            )
            if expected is not None and canonical != expected.to_node_properties():
                raise ValueError("conflicting Decision provenance")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProposalLifecycleError("malformed Decision provenance") from exc

    def _acceptance_decision_edges(self, proposal_id: str) -> list[EdgeRecord]:
        edges = self._storage.list_edges("ACCEPTED_BY_DECISION", limit=10_001)
        if len(edges) > 10_000:
            raise StorageUnavailable("Decision provenance validation budget exceeded")
        return [
            edge
            for edge in edges
            if edge.from_label == "Proposal" and edge.from_id == proposal_id
        ]

    def _require_acceptance_decision_id(self, proposal_id: str) -> str:
        decision_edges = self._acceptance_decision_edges(proposal_id)
        if not decision_edges:
            raise ProposalLifecycleError(
                "Accepted Proposal lacks Decision provenance and cannot convert"
            )
        if len(decision_edges) != 1:
            raise ProposalLifecycleError(
                "Accepted Proposal has ambiguous Decision provenance and cannot convert"
            )
        decision_edge = decision_edges[0]
        if decision_edge.to_label != "Decision":
            raise ProposalLifecycleError(
                "Accepted Proposal lacks Decision provenance and cannot convert"
            )
        decision_node = self._storage.get_node("Decision", decision_edge.to_id)
        if decision_node is None:
            raise ProposalLifecycleError(
                "Accepted Proposal lacks Decision provenance and cannot convert"
            )
        self._validate_decision_node(decision_node)
        return decision_edge.to_id

    @staticmethod
    def _proposal_from_node(node: NodeRecord) -> Proposal:
        try:
            if node.label != "Proposal":
                raise ValueError("invalid proposal label")
            properties = validate_canonical_work_object_properties(
                node.label,
                node.id,
                node.properties,
                archived=node.archived,
                allow_unknown=True,
            )
            created_at = datetime.fromisoformat(properties["created_at"])
            updated_at = datetime.fromisoformat(properties["updated_at"])
            return Proposal(
                id=node.id,
                title=properties["title"],
                description=properties["description"],
                status=WorkStatus(properties["status"]),
                created_at=created_at,
                updated_at=updated_at,
                version=properties["version"],
                artifact_ids=tuple(properties["artifact_ids"]),
                external_link_ids=tuple(properties["external_link_ids"]),
                priority=properties["priority"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProposalLifecycleError(
                "Proposal has unreadable persisted properties"
            ) from exc
