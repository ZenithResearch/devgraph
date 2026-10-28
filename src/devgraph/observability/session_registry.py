"""Private canonical storage seam, not an HTTP admission or authority bypass.

Authorization belongs to the invoking receiver, as with legacy Hermes attachment.
The signed session.attach receiver is the public admission path.
"""

from __future__ import annotations

from devgraph.model.validation import validate_version
from devgraph.observability.session_ref import (
    SESSION_LINK_LABEL,
    SESSION_REF_LABEL,
    SessionLink,
    SessionRef,
    work_ref,
)
from devgraph.storage.base import GraphStorage


class SessionRegistry:
    def __init__(self, storage: GraphStorage) -> None:
        self.storage = storage

    def _put(self, ref: SessionRef) -> SessionRef:
        node = self.storage.get_node(SESSION_REF_LABEL, ref.id)
        if node is not None:
            # Preserve first-import authority and time, even on a later re-observation.
            return SessionRef.from_node_properties(node.id, node.properties)
        self.storage.create_node(SESSION_REF_LABEL, ref.id, ref.to_node_properties())
        return ref

    def put(self, ref: SessionRef) -> SessionRef:
        with self.storage.transaction():
            return self._put(ref)

    def link(self, origin: SessionRef, target: SessionRef, link: SessionLink) -> None:
        if link.origin != origin.identity or link.target != target.identity:
            raise ValueError("session_link_endpoint_conflict")
        with self.storage.transaction():
            previous = self.storage.get_node(SESSION_LINK_LABEL, link.id)
            if previous is not None:
                old = {"kind": SESSION_LINK_LABEL, **previous.properties}
                if old != link.to_node_properties():
                    raise ValueError("session_event_conflict")
                return
            self._put(origin)
            self._put(target)
            self.storage.create_node(SESSION_LINK_LABEL, link.id, link.to_node_properties())
            self.storage.create_edge(
                SESSION_LINK_LABEL, link.id, "SESSION_LINK_ORIGIN", SESSION_REF_LABEL, origin.id
            )
            self.storage.create_edge(
                SESSION_LINK_LABEL, link.id, "SESSION_LINK_TARGET", SESSION_REF_LABEL, target.id
            )

    def attach(
        self,
        ref: SessionRef,
        *,
        work: str,
        expected_version: int,
        basis: str = "explicit",
        observed_at: str | None = None,
    ) -> None:
        kind, node_id = work_ref(work)
        validate_version(expected_version)
        if basis not in {"explicit", "inspected", "declared_scope", "source_observed"}:
            raise ValueError("invalid_session_attribution_basis")
        properties = {"basis": basis, "work_version": expected_version}
        if observed_at is not None:
            from devgraph.observability.session_contract import observed_time

            observed_time(observed_at)
            properties["observed_at"] = observed_at
        with self.storage.transaction():
            node = self.storage.get_node(kind, node_id)
            if node is None:
                raise KeyError("missing_session_work_subject")
            if node.properties.get("version") != expected_version:
                raise ValueError("session_work_version_conflict")
            self._put(ref)
            context = self.storage.session_ref_context(ref.id, limit=100)
            if any(binding["key"] == work for binding in context["work_refs"]):
                return  # Retain the first admitted attribution basis and observation.
            if context["context_bounded"]:
                raise ValueError("session_attribution_capacity")
            self.storage.create_edge(
                kind,
                node_id,
                "ATTRIBUTED_TO_SESSION",
                SESSION_REF_LABEL,
                ref.id,
                properties,
            )
