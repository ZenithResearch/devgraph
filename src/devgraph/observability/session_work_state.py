"""DevGraph-owned session disposition; native turn state cannot change it.

Stored separately so immutable SessionRef identity and first-import authority
remain byte-for-byte compatible. The logical mutation resource is SessionRef.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from devgraph.model.repository import (
    InvalidStatusTransitionError,
    MissingWorkObjectError,
    WorkObjectVersionConflictError,
)
from devgraph.model.validation import validate_version
from devgraph.observability.session_ref import SessionRef

LABEL = "SessionWorkState"


@dataclass(frozen=True)
class SessionWorkState:
    id: str
    state: str = "unreviewed"
    version: int = 1
    updated_at: str | None = None
    updated_by: str | None = None
    correlation_id: str | None = None

    @property
    def kind(self):
        return "SessionRef"

    def public(self):
        return {k: v for k, v in asdict(self).items() if k not in {"id", "correlation_id"}}


def read_state(storage, ref_id):
    node = storage.get_node(LABEL, ref_id)
    if node is None:
        return SessionWorkState(ref_id)
    props = {k: v for k, v in node.properties.items() if k != "kind"}
    value = SessionWorkState(ref_id, **props)
    validate_version(value.version)
    if value.state not in {"open", "done"}:
        raise ValueError("invalid_session_work_state")
    if not value.updated_at or not value.updated_by or not value.correlation_id:
        raise ValueError("invalid_session_work_state")
    return value


def set_state(storage, ref_id, expected_version, state, *, actor_id, session_id, correlation_id):
    # Caller owns the serialized mutation transaction, signature checks and receipt.
    node = storage.get_node("SessionRef", ref_id)
    if node is None:
        raise MissingWorkObjectError("Session reference not found.")
    ref = SessionRef.from_node_properties(node.id, node.properties)
    # Validate receiver-owned authority fields through the same closed identity model.
    SessionRef(ref.identity, actor_id, session_id, correlation_id)
    current = read_state(storage, ref_id)
    if current.version != expected_version:
        raise WorkObjectVersionConflictError(
            "SessionRef", ref_id, expected_version=expected_version, actual_version=current.version
        )
    if state not in {"open", "done"}:
        raise ValueError("invalid_session_work_state")
    if state == current.state:
        raise InvalidStatusTransitionError("Session work state is unchanged.")
    validate_version(current.version + 1)
    value = SessionWorkState(
        ref_id,
        state,
        current.version + 1,
        datetime.now(timezone.utc).isoformat(),
        actor_id,
        correlation_id,
    )
    props = {k: v for k, v in asdict(value).items() if k != "id"}
    if storage.get_node(LABEL, ref_id) is None:
        storage.create_node(LABEL, ref_id, props)
    else:
        storage.update_node(LABEL, ref_id, props)
    return value
