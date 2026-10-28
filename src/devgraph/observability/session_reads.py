"""Bounded canonical metadata reads; authorization belongs to the façade."""

import re

from devgraph.model.validation import validate_page_limit
from devgraph.observability.session_ref import SessionRef

REF_ID = re.compile(r"sr-[0-9a-f]{64}")


def public_reference(node):
    ref = SessionRef.from_node_properties(node.id, node.properties)
    return {
        "id": ref.id,
        "application": ref.identity.application,
        "source_instance_id": ref.identity.source_instance_id,
        "external_session_id": ref.identity.external_session_id,
        "imported_at": ref.created_at.isoformat(),
        "imported_by": ref.actor_id,
        "canonical": True,
    }


def read_references(storage, *, ref_id=None, after=None, limit=50):
    validate_page_limit(limit)
    if any(value is not None and not REF_ID.fullmatch(value) for value in (ref_id, after)):
        raise ValueError("invalid_session_reference")
    if ref_id:
        node = storage.get_node("SessionRef", ref_id)
        if node is None:
            return None
        value = public_reference(node)
        value.update(storage.session_ref_context(ref_id, limit=limit))
        return value
    nodes = storage.query("SessionRef", after_id=after, limit=limit)
    return {
        "schema": "devgraph.session-refs.v1",
        "items": [public_reference(n) for n in nodes],
        "next_after": nodes[-1].id if len(nodes) == limit else None,
    }
