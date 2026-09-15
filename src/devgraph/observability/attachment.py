"""Attachment service linking work/evidence to HermesSessionRef records.

`ATTRIBUTED_TO_HERMES_SESSION` is provenance: subject → reference. The
service persists the reference node (idempotently reusing it across
subjects) and creates the edge inside one storage transaction, failing
closed with no partial writes when the subject does not exist.
"""

from __future__ import annotations

from devgraph.observability.hermes_session_ref import (
    HERMES_SESSION_REF_LABEL,
    HermesSessionRef,
)
from devgraph.storage.base import GraphStorage

ATTRIBUTED_TO_HERMES_SESSION = "ATTRIBUTED_TO_HERMES_SESSION"


class HermesSessionAttachmentService:
    def __init__(self, storage: GraphStorage) -> None:
        self._storage = storage

    def attach(
        self,
        ref: HermesSessionRef,
        *,
        subject_label: str,
        subject_id: str,
    ) -> HermesSessionRef:
        with self._storage.transaction():
            subject = self._storage.get_node(subject_label, subject_id)
            if subject is None:
                raise KeyError(f"missing subject {subject_label}:{subject_id}")
            if self._storage.get_node(HERMES_SESSION_REF_LABEL, ref.id) is None:
                self._storage.create_node(
                    HERMES_SESSION_REF_LABEL, ref.id, ref.to_node_properties()
                )
            self._storage.create_edge(
                subject_label,
                subject_id,
                ATTRIBUTED_TO_HERMES_SESSION,
                HERMES_SESSION_REF_LABEL,
                ref.id,
            )
        return ref
