"""Private receiver-side outbox consumer; never trusts the source for authority.

No public route is registered. The eventual authorized receiver supplies its
own AuthorityContext and exact current Work versions after authorization.
"""

from __future__ import annotations

from datetime import datetime

from devgraph.auth.context import AuthorityContext
from devgraph.observability.session_capture import SessionJournal
from devgraph.observability.session_ref import SessionIdentity, SessionLink, SessionRef
from devgraph.observability.session_registry import SessionRegistry


def import_attempt(
    registry: SessionRegistry,
    journal: SessionJournal,
    attempt_id: str,
    *,
    authority: AuthorityContext,
    expected_work_versions: dict[str, int],
) -> SessionRef:
    observation = journal.attempt(attempt_id)
    dispatch = observation["dispatch"]
    if set(expected_work_versions) != set(dispatch["work_refs"]):
        raise ValueError("session_work_scope_conflict")
    child_identity = SessionIdentity(**observation["native"]["identity"])
    # Bind even a corrupted outbox's native entry to the original dispatch.
    if (
        child_identity.application != dispatch["application"]
        or child_identity.source_instance_id != dispatch["source_instance_id"]
        or observation["native"]["attempt_id"] != dispatch["attempt_id"]
    ):
        raise ValueError("session_dispatch_identity_conflict")
    observed = datetime.fromisoformat(observation["observed_at"].replace("Z", "+00:00"))

    def ref(identity: SessionIdentity) -> SessionRef:
        return SessionRef(
            identity, authority.actor_id, authority.session_id, authority.correlation_id, observed
        )

    child = ref(child_identity)
    with registry.storage.transaction():
        child = registry.put(child)
        if dispatch["parent"] is not None:
            parent = ref(SessionIdentity(**dispatch["parent"]))
            registry.link(
                parent,
                child,
                SessionLink(
                    dispatch["source_instance_id"],
                    attempt_id,
                    attempt_id,
                    "delegated_to",
                    parent.identity,
                    child.identity,
                    observed,
                ),
            )
        for work, version in expected_work_versions.items():
            # Attribution denotes declared Work scope, not successful execution.
            registry.attach(child, work=work, expected_version=version)
    return child
