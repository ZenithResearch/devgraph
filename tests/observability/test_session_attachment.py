import json
from datetime import datetime, timezone

import pytest

from devgraph.model.repository import WorkObjectRepository, WorkObjectVersionConflictError
from devgraph.model.work import Task
from devgraph.named_work import NamedWorkMutations
from devgraph.observability.session_ref import SessionIdentity, SessionRef
from devgraph.storage.memory import MemoryGraphStorage
from devgraph.work_requests import InvalidWorkRequest, WorkRequest


def request(**changes):
    value = {
        "schema": "devgraph.work-request.v2",
        "operation": "session.attach",
        "kind": "Task",
        "id": "work",
        "expected_version": 1,
        "payload": {
            "session": {
                "application": "codex",
                "source_instance_id": "native-store",
                "external_session_id": "native-id",
            },
            "observed_at": "2026-10-27T20:00:00Z",
            "basis": "inspected",
        },
    }
    value.update(changes)
    return value


def parse(value):
    return WorkRequest.from_json(json.dumps(value).encode())


def test_exact_signed_resources_and_legacy_rejection():
    parsed = parse(request())
    identity = SessionIdentity("codex", "native-store", "native-id")
    assert parsed.resources == ("SessionRef/" + identity.id, "Task/work")
    assert parsed.authority_operation == "devgraph.work.session.attach.v2"
    assert parsed.payload["link"] is None
    with pytest.raises(InvalidWorkRequest):
        parse(request(schema="devgraph.work-request.v1"))
    with pytest.raises(InvalidWorkRequest):
        parse(request(kind="Todo"))


def test_signed_link_derives_both_refs_and_one_event_resource():
    value = request()
    native = value["payload"]["session"]
    value["payload"]["link"] = {
        "source_instance_id": "hermes-store",
        "event_id": "event",
        "attempt_id": "attempt",
        "relation": "uses_runtime",
        "origin": {
            "application": "hermes",
            "source_instance_id": "hermes-store",
            "external_session_id": "parent",
        },
        "target": native,
        "observed_at": value["payload"]["observed_at"],
        "basis": "source_observed",
    }
    parsed = parse(value)
    assert len(parsed.resources) == 4
    assert sum(r.startswith("SessionRef/") for r in parsed.resources) == 2
    assert sum(r.startswith("SessionLink/") for r in parsed.resources) == 1
    value["payload"]["session"]["external_session_id"] = "unrelated"
    # Use an independent target: both endpoints must not be detached from subject.
    value["payload"]["link"]["target"] = {**native, "external_session_id": "other"}
    with pytest.raises(InvalidWorkRequest):
        parse(value)


def test_attribution_preserves_native_and_authority_identity_and_rolls_back_stale_work():
    storage = MemoryGraphStorage()
    repository = WorkObjectRepository(storage)
    repository.create(Task(id="work", title="Work"))
    parsed = parse(request())
    mutations = NamedWorkMutations(storage)
    result = mutations.execute(
        parsed, actor_id="importing-actor", session_id="authority-session", correlation_id="corr"
    )
    assert result.version == 2 and result.progress == "not_started"
    native = SessionIdentity(**parsed.payload["session"])
    ref = SessionRef.from_node_properties(
        native.id, storage.get_node("SessionRef", native.id).properties
    )
    assert ref.identity.external_session_id == "native-id"
    assert ref.session_id == "authority-session"
    edge = storage.list_edges("ATTRIBUTED_TO_SESSION")[0]
    assert edge.properties["basis"] == "inspected"
    with pytest.raises(WorkObjectVersionConflictError):
        mutations.execute(parsed, actor_id="other", session_id="other", correlation_id="other")
    assert len(storage.query("SessionRef")) == 1


def test_missing_verified_authority_cannot_create_partial_session_record():
    storage = MemoryGraphStorage()
    WorkObjectRepository(storage).create(Task(id="work", title="Work"))
    with pytest.raises(ValueError):
        NamedWorkMutations(storage).execute(parse(request()))
    assert not storage.query("SessionRef")
    assert WorkObjectRepository(storage).get_by_id("Task", "work").version == 1


def test_general_ref_legacy_missing_kind_remains_readable():
    ref = SessionRef(
        SessionIdentity("codex", "native", "id"),
        "actor",
        "auth",
        "corr",
        datetime.now(timezone.utc),
    )
    properties = ref.to_node_properties()
    properties.pop("kind")
    assert SessionRef.from_node_properties(ref.id, properties) == ref
