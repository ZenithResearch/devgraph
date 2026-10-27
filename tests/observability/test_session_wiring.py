"""Source binding, authenticated import, rollback and canonical read boundaries."""

import json
import sqlite3
from contextlib import nullcontext
from types import MethodType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import FAKE_CREDENTIAL, build_services
from tests.auth.test_secs_work import KEY
from tests.auth.test_secs_work_v2 import proof_v2, services_v2
from tests.observability.test_session_attachment import parse, request
from tests.observability.test_session_sources import hermes_db, source

from devgraph.api import create_app
from devgraph.auth.scopes import SCOPE_READ
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Task
from devgraph.observability.session_prepare import prepare, write_bundle
from devgraph.observability.session_read_runtime import SessionReader
from devgraph.observability.session_ref import SessionIdentity, SessionLink
from devgraph.observability.session_sources import NativeSources
from devgraph.storage.neo4j import Neo4jGraphStorage


def native_binding(tmp_path):
    hermes = tmp_path / "hermes"
    codex = tmp_path / "codex"
    hermes.mkdir()
    codex.mkdir()
    hermes_db(hermes)
    with sqlite3.connect(hermes / "state.db") as db:
        db.execute("ALTER TABLE sessions ADD COLUMN model_config TEXT")
        db.execute(
            "UPDATE sessions SET model_config=? WHERE id='child'",
            (json.dumps({"codex_thread_id": "native", "secret": "PRIVATE"}),),
        )
    path = codex / "sessions" / "retained.jsonl"
    path.parent.mkdir()
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "native"}}) + "\n")
    with sqlite3.connect(codex / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads(id,updated_at,created_at,cwd,source,rollout_path)")
        db.execute("INSERT INTO threads VALUES('native',100,99,NULL,'app-server',?)", (str(path),))
    sources = [
        {**source(hermes), "id": "hermes-store"},
        {**source(codex, "codex"), "id": "codex-store"},
    ]
    return sources, path


def test_durable_native_binding_requires_exact_record_and_keeps_runtime_semantics(tmp_path):
    sources, path = native_binding(tmp_path)
    reader = SessionReader({"sources": sources, "observer_journal": str(tmp_path / "journal")})
    rows = reader.snapshot()["items"]
    parent = next(r for r in rows if r["external_session_id"] == "child")
    link = next(item for item in parent["links"] if item["relation"] == "uses_runtime")
    assert link["witness"]["origin"]["external_session_id"] == "child"
    assert link["witness"]["target"]["external_session_id"] == "native"
    next(r for r in reader.value["items"] if r["id"] == parent["id"])["work_refs"].append(
        {"key": "Task/work", "basis": "inspected_work", "observed_at": "2026-10-27T20:00:00Z"}
    )
    reader.work_reader = lambda key: {"key": key, "title": "Work", "version": 1}
    prepared = prepare(reader, parent["id"], "Task/work", link_ref=link["id"])
    assert prepared.payload["link"]["relation"] == "uses_runtime"
    first = link["witness"]
    reader.refresh(force=True)
    parent = next(r for r in reader.value["items"] if r["external_session_id"] == "child")
    assert (
        next(item for item in parent["links"] if item["relation"] == "uses_runtime")["witness"]
        == first
    )
    assert "PRIVATE" not in json.dumps(rows)
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "different"}}))
    reader.refresh(force=True)
    parent = next(r for r in reader.value["items"] if r["external_session_id"] == "child")
    assert parent["unresolved_native_binding"]["reason"] == "source_unavailable"
    assert not any(item["relation"] == "uses_runtime" for item in parent["links"])


def test_binding_ambiguous_across_configured_stores_is_unresolved(tmp_path):
    sources, _ = native_binding(tmp_path)
    rows, _ = NativeSources([*sources, {**sources[1], "id": "second-store"}]).scan()
    parent = next(r for r in rows if r["external_session_id"] == "child")
    assert parent["unresolved_native_binding"]["reason"] == "ambiguous_store"


def test_signed_receiver_attaches_verified_authority_and_replays_receipt():
    services, _ = services_v2()
    WorkObjectRepository(services.storage).create(Task(id="work", title="Work"))
    raw = parse(request()).canonical
    arguments = dict(request_json=raw, idempotency_key=KEY, projection_json=proof_v2(raw))
    first = services.named_work_v2.execute(**arguments)
    duplicate = services.named_work_v2.execute(**arguments)
    assert duplicate[1].id == first[1].id and duplicate[2]
    node = services.storage.query("SessionRef")[0]
    assert node.properties["session_id"] == "11" * 16
    assert node.properties["external_session_id"] == "native-id"
    assert WorkObjectRepository(services.storage).get_by_id("Task", "work").version == 2


def test_canonical_routes_auth_page_context_validation_and_read_cannot_write():
    services = build_services(frozenset({SCOPE_READ}))
    storage = services.storage
    WorkObjectRepository(storage).create(Task(id="work", title="Work"))
    parsed = parse(request())
    from devgraph.named_work import NamedWorkMutations

    NamedWorkMutations(storage).execute(
        parsed, actor_id="actor", session_id="auth", correlation_id="corr"
    )
    client = TestClient(create_app(services))
    headers = {"Authorization": "Bearer " + FAKE_CREDENTIAL}
    assert client.get("/session-refs/v1").status_code == 401
    page = client.get("/session-refs/v1", headers=headers).json()
    ref = page["items"][0]
    detail = client.get("/session-refs/v1/" + ref["id"], headers=headers).json()
    assert detail["canonical"] and detail["work_refs"][0]["key"] == "Task/work"
    assert client.get("/session-refs/v1/bad", headers=headers).status_code == 422
    assert client.post("/session-refs/v1", headers=headers, json={}).status_code == 405
    assert client.post("/work-operations/v2", headers=headers, json={}).status_code in (
        401,
        403,
        422,
    )


def test_prepare_requires_observed_resolved_work_and_writes_private_review(tmp_path):
    identity = SessionIdentity("codex", "store", "native")
    detail = {
        "id": identity.id,
        **identity.__dict__,
        "source_resolution": "available",
        "links": [],
        "related_work": [
            {
                "key": "Task/work",
                "basis": "successful_cli_work_read",
                "resolution": "available",
                "version": 7,
                "observed_at": "2026-10-27T20:00:00Z",
            }
        ],
    }
    reader = SimpleNamespace(detail=lambda _: detail)
    prepared = prepare(reader, identity.id, "Task/work")
    assert prepared.expected_version == 7
    result = write_bundle(prepared, tmp_path / "review")
    assert result["submitted"] is False
    assert (tmp_path / "review/request.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_bundle(prepared, tmp_path / "review")
    detail["related_work"][0]["basis"] = "folder_guess"
    with pytest.raises(ValueError, match="no_observed"):
        prepare(reader, identity.id, "Task/work")


def test_canonical_read_unavailable_stays_unknown(tmp_path):
    hermes_db(tmp_path)
    reader = SessionReader(
        {"sources": [source(tmp_path)], "canonical_reads": True}, graph_reader=lambda _: None
    )
    page = reader.snapshot()
    assert page["canonical_graph"]["status"] == "unavailable"
    assert all(r["canonical"] is None for r in page["items"])


def test_canonical_details_keep_one_work_row_and_source_evidence(tmp_path):
    hermes_db(tmp_path)
    identity = SessionIdentity("hermes", "test-source", "child")
    canonical = {
        "id": identity.id,
        "canonical": True,
        "work_refs": [{"key": "Task/work", "basis": "inspected", "work_version": 1}],
        "links": [],
    }

    def graph_read(path):
        if path == "/session-refs/v1?limit=100":
            return {"schema": "devgraph.session-refs.v1", "items": [canonical], "next_after": None}
        return canonical

    reader = SessionReader(
        {"sources": [source(tmp_path)], "canonical_reads": True},
        graph_reader=graph_read,
        work_reader=lambda key: {"key": key, "title": "Work", "version": 2},
    )
    reader.refresh()
    item = next(r for r in reader.value["items"] if r["id"] == identity.id)
    item["work_refs"].append(
        {
            "key": "Task/work",
            "basis": "successful_cli_work_read",
            "observed_at": "2026-10-27T20:00:00Z",
        }
    )
    detail = reader.detail(identity.id)
    assert detail["canonical_status"] == "imported"
    assert len(detail["related_work"]) == 1
    assert detail["related_work"][0]["canonical"] is True
    assert detail["related_work"][0]["basis"] == "successful_cli_work_read"


def test_neo4j_session_context_uses_link_edges_and_decodes_generic_values():
    storage = object.__new__(Neo4jGraphStorage)
    origin, target = SessionIdentity("hermes", "store", "h"), SessionIdentity("codex", "store", "c")
    from datetime import datetime, timezone

    link = SessionLink(
        "store",
        "event",
        "attempt",
        "uses_runtime",
        origin,
        target,
        datetime.now(timezone.utc),
        "source_observed",
    )
    properties = {
        **Neo4jGraphStorage._generic_properties(link.to_node_properties()),
        "id": link.id,
        "archived": False,
    }
    calls = []

    def run(self, query, **parameters):
        calls.append((query, parameters))
        return (
            []
            if len(calls) == 1
            else [
                {
                    "id": link.id,
                    "labels": ["SessionLink"],
                    "archived": False,
                    "properties": properties,
                }
            ]
        )

    storage._run_graph = MethodType(run, storage)
    storage.transaction = lambda: nullcontext()
    context = storage.session_ref_context(origin.id)
    assert context["links"][0]["relation"] == "uses_runtime"
    assert "SESSION_LINK_ORIGIN|SESSION_LINK_TARGET" in calls[1][0]
    assert calls[1][1]["ref_id"] == origin.id


def test_paired_attachment_needs_one_complete_rule_and_postcheck_rolls_back():
    from tests.auth.test_paired_resources import shared_host
    from tests.auth.test_write_scope import execute, fixture

    from devgraph.auth.secs_work import SecSWorkDenied

    parsed = parse(request())
    resources = [
        dict(kind=key.split("/", 1)[0], ids=[key.split("/", 1)[1]]) for key in parsed.resources
    ]
    rules = [
        dict(operation=parsed.authority_operation, resources=[resource]) for resource in resources
    ]
    adapter, storage, audit, _ = fixture(rules)
    adapter.compound_host = shared_host()
    WorkObjectRepository(storage).create(Task(id="work", title="Work"))
    with pytest.raises(SecSWorkDenied, match="compound_rule_not_granted"):
        execute(adapter, parsed)
    assert not storage.query("SessionRef") and not audit.records
    whole = dict(operation=parsed.authority_operation, resources=resources)
    adapter, storage, audit, _ = fixture([whole])
    adapter.compound_host = shared_host()
    WorkObjectRepository(storage).create(Task(id="work", title="Work"))
    original = adapter.mutations.execute

    def corrupt(request, **kwargs):
        result = original(request, **kwargs)
        storage._nodes.pop(("SessionRef", request.resources[0].split("/", 1)[1]))
        return result

    adapter.mutations.execute = corrupt
    with pytest.raises(SecSWorkDenied, match="compound_postcondition_denied"):
        execute(adapter, parsed)
    assert not storage.query("SessionRef") and not audit.records
    assert WorkObjectRepository(storage).get_by_id("Task", "work").version == 1
    adapter.mutations.execute = original
    work, _, duplicate = execute(adapter, parsed)
    assert work.version == 2 and not duplicate
