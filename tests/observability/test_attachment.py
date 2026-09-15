"""Commit 2: HermesSessionRef attachment via ATTRIBUTED_TO_HERMES_SESSION.

The attachment service persists the reference node and links subjects
(Todo-derived work, Artifact, Decision, EventReceipt) to it. It never
mutates the subject records. All fixtures are synthetic.
"""

from __future__ import annotations

import pytest

from devgraph.observability.attachment import (
    ATTRIBUTED_TO_HERMES_SESSION,
    HermesSessionAttachmentService,
)
from devgraph.observability.hermes_session_ref import (
    HERMES_SESSION_REF_LABEL,
    HermesSessionRef,
)
from devgraph.storage.memory import MemoryGraphStorage


def _ref(ref_id: str = "hsr-1") -> HermesSessionRef:
    return HermesSessionRef(
        id=ref_id,
        external_session_id="hermes-session-synthetic-001",
        actor_id="agent-frank",
        session_id="session-0001",
        correlation_id="corr-0001",
        token_count_total=200,
    )


@pytest.fixture
def storage() -> MemoryGraphStorage:
    storage = MemoryGraphStorage()
    storage.create_node("Issue", "issue-1", {"title": "synthetic issue"})
    storage.create_node("Artifact", "artifact-1", {"title": "synthetic artifact"})
    storage.create_node("Decision", "decision-1", {"title": "synthetic decision"})
    storage.create_node("EventReceipt", "receipt-1", {"operation": "synthetic"})
    return storage


class TestAttachment:
    def test_predicate_name_is_pinned(self) -> None:
        assert ATTRIBUTED_TO_HERMES_SESSION == "ATTRIBUTED_TO_HERMES_SESSION"

    @pytest.mark.parametrize(
        ("subject_label", "subject_id"),
        [
            ("Issue", "issue-1"),
            ("Artifact", "artifact-1"),
            ("Decision", "decision-1"),
            ("EventReceipt", "receipt-1"),
        ],
    )
    def test_attach_links_subject_to_ref(
        self, storage: MemoryGraphStorage, subject_label: str, subject_id: str
    ) -> None:
        service = HermesSessionAttachmentService(storage)
        ref = service.attach(_ref(), subject_label=subject_label, subject_id=subject_id)

        assert storage.get_node(HERMES_SESSION_REF_LABEL, ref.id) is not None
        edges = storage.list_edges(ATTRIBUTED_TO_HERMES_SESSION)
        assert len(edges) == 1
        edge = edges[0]
        assert (edge.from_label, edge.from_id) == (subject_label, subject_id)
        assert (edge.to_label, edge.to_id) == (HERMES_SESSION_REF_LABEL, ref.id)

    def test_ref_node_is_reused_across_subjects(
        self, storage: MemoryGraphStorage
    ) -> None:
        service = HermesSessionAttachmentService(storage)
        service.attach(_ref(), subject_label="Issue", subject_id="issue-1")
        service.attach(_ref(), subject_label="Artifact", subject_id="artifact-1")

        refs = storage.query(HERMES_SESSION_REF_LABEL)
        assert [node.id for node in refs] == ["hsr-1"]
        assert len(storage.list_edges(ATTRIBUTED_TO_HERMES_SESSION)) == 2

    def test_subject_records_are_not_mutated(
        self, storage: MemoryGraphStorage
    ) -> None:
        before = storage.get_node("Issue", "issue-1")
        service = HermesSessionAttachmentService(storage)
        service.attach(_ref(), subject_label="Issue", subject_id="issue-1")
        after = storage.get_node("Issue", "issue-1")
        assert before == after

    def test_missing_subject_fails_closed_without_partial_writes(
        self, storage: MemoryGraphStorage
    ) -> None:
        service = HermesSessionAttachmentService(storage)
        with pytest.raises(KeyError):
            service.attach(_ref(), subject_label="Issue", subject_id="missing")
        assert storage.query(HERMES_SESSION_REF_LABEL) == []
        assert storage.list_edges(ATTRIBUTED_TO_HERMES_SESSION) == []
