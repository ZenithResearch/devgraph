"""Canonical disposition survives source activity, retries and stale writers."""

import json

import pytest
from fastapi.testclient import TestClient
from tests.auth.test_secs_work import KEY
from tests.auth.test_secs_work_v2 import proof_v2, services_v2
from tests.observability.test_session_attachment import parse, request

from devgraph.api import create_app
from devgraph.auth.secs_work import SecSWorkDenied
from devgraph.model.repository import WorkObjectRepository, WorkObjectVersionConflictError
from devgraph.model.work import Task
from devgraph.named_work import NamedWorkMutations
from devgraph.observability.session_prepare import prepare_state
from devgraph.observability.session_reads import read_references
from devgraph.observability.session_ref import SessionIdentity
from devgraph.observability.session_work_state import read_state
from devgraph.work_requests import InvalidWorkRequest

REF = SessionIdentity("codex", "native-store", "native-id").id


def state_request(state="done", version=1, **changes):
    value = dict(
        schema="devgraph.work-request.v2",
        operation="session.state",
        kind="SessionRef",
        id=REF,
        expected_version=version,
        payload={"state": state},
    )
    value.update(changes)
    return value


def setup():
    services, audit = services_v2()
    WorkObjectRepository(services.storage).create(Task(id="work", title="Work"))
    mutations = NamedWorkMutations(services.storage)
    args = dict(actor_id="actor", session_id="authority-session", correlation_id="corr")
    mutations.execute(parse(request()), **args)
    return services, audit, mutations, args


def test_exact_resource_canonical_vectors_and_invalid_shapes():
    value = parse(state_request())
    assert value.resources == ("SessionRef/" + REF,)
    assert value.authority_operation == "devgraph.work.session.state.v2"
    for invalid in [
        state_request(kind="Task"),
        state_request(id="work"),
        state_request(schema="devgraph.work-request.v1"),
        state_request(payload={"state": "running"}),
        state_request(payload={"state": "done", "native_status": "ended"}),
        state_request(expected_version=0),
        state_request(expected_version=True),
        state_request(operation="patch", payload={"title": "bad"}),
    ]:
        with pytest.raises(InvalidWorkRequest):
            parse(invalid)


def test_completion_and_reopen_leave_identity_linked_work_and_other_sessions_untouched():
    services, _, mutations, args = setup()
    storage = services.storage
    original = storage.get_node("SessionRef", REF)
    assert read_state(storage, REF).state == "unreviewed"
    done = mutations.execute(parse(state_request()), **args)
    assert done.state == "done" and done.version == 2 and done.updated_by == "actor"
    assert storage.get_node("SessionRef", REF) == original
    work = WorkObjectRepository(storage).get_by_id("Task", "work")
    assert work.version == 2 and work.progress == "not_started"
    # Re-observation/import cannot reopen a completed session or erase its actor/time.
    mutations.execute(parse(request(expected_version=2)), **args)
    assert read_state(storage, REF) == done
    with pytest.raises(WorkObjectVersionConflictError):
        mutations.execute(parse(state_request("open", 1)), **args)
    assert read_state(storage, REF) == done
    reopened = mutations.execute(parse(state_request("open", 2)), **args)
    assert reopened.version == 3 and reopened.state == "open"
    assert read_references(storage, ref_id=REF)["work_state"]["state"] == "open"
    assert read_references(storage)["items"][0]["work_state"]["version"] == 3


def test_signed_receiver_replays_one_receipt_and_payload_substitution_fails():
    services, audit, _, _ = setup()
    raw = parse(state_request()).canonical
    arguments = dict(request_json=raw, projection_json=proof_v2(raw), idempotency_key=KEY)
    first = services.named_work_v2.execute(**arguments)
    duplicate = services.named_work_v2.execute(**arguments)
    assert first[0].version == 2 and duplicate[0] is None and duplicate[2]
    assert duplicate[1].id == first[1].id
    assert audit.records[0].safe_summary["session_work_state"] == "done"
    changed = parse(state_request("open", 2)).canonical
    with pytest.raises(SecSWorkDenied):
        services.named_work_v2.execute(**{**arguments, "request_json": changed})
    assert read_state(services.storage, REF).state == "done"


def test_http_session_response_is_not_a_todo_and_anonymous_writes_are_denied():
    services, _, _, _ = setup()
    client = TestClient(create_app(services))
    raw = parse(state_request()).canonical
    assert client.post("/todo-operations/v2", content=raw).status_code == 403
    import base64

    headers = {
        "X-Devgraph-Work-Authority": base64.urlsafe_b64encode(proof_v2(raw)).rstrip(b"=").decode(),
        "Idempotency-Key": KEY,
    }
    response = client.post("/todo-operations/v2", content=raw, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["work"] is None
    assert response.json()["session"]["work_state"]["state"] == "done"


def test_sdk_decodes_session_results_and_retries_without_inventing_a_todo():
    from devgraph.client.http import DevgraphHttpClient, DevgraphWorkV2Context

    services, _, _, _ = setup()
    client = DevgraphHttpClient(
        transport=TestClient(create_app(services)), base_url="http://testserver", timeout=10
    )
    raw = parse(state_request()).canonical
    context = DevgraphWorkV2Context(projection_json=proof_v2(raw))
    first = client.execute_named_work(context, request_json=raw, idempotency_key=KEY)
    assert first.work is None and first.session.work_state.state == "done"
    replay = client.execute_named_work(context, request_json=raw, idempotency_key=KEY)
    assert replay.receipt.duplicate and replay.session is None and replay.work is None


def test_prepare_uses_canonical_revision_even_with_missing_native_source():
    from types import SimpleNamespace

    reader = SimpleNamespace(
        graph_reader=lambda path: {
            "id": REF,
            "canonical": True,
            "work_state": {"state": "done", "version": 9},
        }
    )
    prepared = prepare_state(reader, REF, "open")
    assert prepared.expected_version == 9 and prepared.resources == ("SessionRef/" + REF,)
    reader.graph_reader = lambda path: {"id": REF, "canonical": True}
    with pytest.raises(ValueError, match="host_upgrade_required"):
        prepare_state(reader, REF, "done")


def test_shared_rust_vectors_match_python():
    from pathlib import Path

    vectors = json.loads(Path(__file__).with_name("session-state-vectors.json").read_text())
    for vector in vectors:
        parsed = parse(json.loads(vector["raw"]))
        assert parsed.canonical.decode() == vector["canonical"]
        assert list(parsed.resources) == vector["resources"]
        assert parsed.digest == vector["digest"]


def test_companion_state_is_canonical_and_source_activity_cannot_reopen_it(tmp_path):
    from tests.observability.test_session_sources import hermes_db, source

    from devgraph.observability.session_read_runtime import SessionReader

    hermes_db(tmp_path)
    native = SessionIdentity("hermes", source(tmp_path)["id"], "child")
    canonical = {
        "id": native.id,
        "canonical": True,
        "work_refs": [],
        "links": [],
        "work_state": {"state": "done", "version": 2},
    }

    def graph(path):
        if path == "/session-refs/v1?limit=100":
            return {"schema": "devgraph.session-refs.v1", "items": [canonical], "next_after": None}
        return canonical

    reader = SessionReader(
        {"sources": [source(tmp_path)], "canonical_reads": True}, graph_reader=graph
    )
    assert reader.detail(native.id)["work_state"]["state"] == "done"
    reader.refresh(force=True)
    assert (
        next(r for r in reader.value["items"] if r["id"] == native.id)["work_state"]
        == canonical["work_state"]
    )
    # Even inside the cached scan window, a failed exact canonical read cannot
    # serve the last Done value as fresh authority.
    reader.graph_reader = lambda _: None
    assert reader.detail(native.id)["work_state"] is None


def test_missing_reference_or_missing_authority_never_leaves_orphan_state():
    services, _, mutations, args = setup()
    with pytest.raises(ValueError):
        mutations.execute(parse(state_request()))
    assert read_state(services.storage, REF).state == "unreviewed"
    from devgraph.model.repository import MissingWorkObjectError

    with pytest.raises(MissingWorkObjectError):
        mutations.execute(parse(state_request(id="sr-" + "f" * 64)), **args)
    assert services.storage.query("SessionWorkState") == []
