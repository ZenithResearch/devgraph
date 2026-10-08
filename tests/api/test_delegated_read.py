from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.arena_requests import ArenaRequest
from devgraph.arenas import ArenaMutations
from devgraph.auth import AuditLog, CredentialEnvelope, LocalDevVerifier
from devgraph.auth.delegated_contract import (
    ArenaReadGrant,
    SimpleReadGrant,
    WorkReadGrant,
)
from devgraph.auth.delegated_read import DelegatedReadService
from devgraph.auth.scopes import SCOPE_READ
from devgraph.model.base import utc_now
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Initiative, Issue, Project, Task

TOKEN = "fake-credential-delegated"


def work_grant(**overrides) -> WorkReadGrant:
    value = {
        "scope": "devgraph.work.read",
        "operations": ["list", "get", "relationships", "children", "blockers"],
        "relationship_types": [
            "children",
            "parent",
            "dependencies",
            "dependents",
            "blockers",
            "blocked",
        ],
        "work_kinds": ["Proposal", "Initiative", "Project", "Issue", "Task"],
        "arena_ids": ["arena-a"],
        "include_archived": False,
    }
    value.update(overrides)
    value = {name: setting for name, setting in value.items() if setting is not None}
    return WorkReadGrant.model_validate(value)


def delegated_client(*grants) -> tuple[TestClient, object, AuditLog]:
    services = build_services(frozenset({SCOPE_READ}))
    verifier = LocalDevVerifier(auth_mode="local-dev")
    scopes = frozenset(grant.scope for grant in grants)
    verifier.register(
        TOKEN,
        CredentialEnvelope(
            actor_id="delegated-test",
            session_id="delegated-session",
            correlation_id="delegated-correlation",
            scopes=scopes,
            expires_at=utc_now() + timedelta(hours=1),
            issuer="devgraph-owner-local-v1",
            audience="devgraph",
            credential_id="dgc-test",
            credential_version=1,
            read_grants=tuple(grants),
            lifecycle_status="active",
            resource="https://work.zenith-research.ca/devgraph",
        ),
    )
    audit = AuditLog()
    delegated = DelegatedReadService(
        storage=services.storage,
        verifier=verifier,
        audience="devgraph",
        audit_log=audit,
    )
    services = replace(services, delegated_read=delegated)
    return TestClient(create_app(services), raise_server_exceptions=False), services, audit


def seed_arena_tree(services) -> None:
    repository = WorkObjectRepository(services.storage)
    root = Initiative(id="root", title="Root")
    project = Project(id="project", title="Project")
    inside = Issue(id="z-inside", title="Inside")
    outside = Issue(id="a-outside", title="Outside")
    source = Task(id="source", title="Source")
    hidden = Task(id="a-hidden", title="Hidden")
    visible = Task(id="z-visible", title="Visible")
    for item in (root, project, inside, outside, source, hidden, visible):
        repository.create(item)
    ArenaMutations(services.storage).execute(
        ArenaRequest.from_json(
            b'{"schema":"devgraph.arena-request.v1","operation":"create",'
            b'"kind":"Arena","id":"arena-a","expected_version":null,'
            b'"payload":{"id":"arena-a","title":"Arena A"}}'
        )
    )
    services.storage.create_edge("Arena", "arena-a", "CONTAINS_WORK", "Initiative", "root")
    services.storage.create_edge("Initiative", "root", "HAS_CHILD", "Project", "project")
    services.storage.create_edge("Project", "project", "HAS_CHILD", "Issue", "z-inside")
    services.storage.create_edge("Issue", "z-inside", "HAS_CHILD", "Task", "source")
    services.storage.create_edge("Issue", "z-inside", "HAS_CHILD", "Task", "z-visible")
    services.storage.create_edge("Task", "a-hidden", "BLOCKS", "Task", "source")
    services.storage.create_edge("Task", "z-visible", "BLOCKS", "Task", "source")


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_delegated_work_filters_before_pagination_and_hides_excluded_resources() -> None:
    client, services, audit = delegated_client(work_grant())
    seed_arena_tree(services)

    page = client.get("/devgraph/work/Issue?limit=1", headers=auth())
    inside = client.get("/devgraph/work/Issue/z-inside", headers=auth())
    outside = client.get("/devgraph/work/Issue/a-outside", headers=auth())

    assert page.status_code == 200
    assert [item["id"] for item in page.json()["items"]] == ["z-inside"]
    assert inside.status_code == 200
    assert outside.status_code == 404
    assert len(audit.records) == 2
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["vary"] == "Authorization, Origin"


def test_relationship_targets_are_independently_filtered_before_pagination() -> None:
    client, services, _ = delegated_client(work_grant())
    seed_arena_tree(services)

    response = client.get(
        "/devgraph/work/Task/source/relationships/blockers?limit=1", headers=auth()
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == ["z-visible"]


def test_archived_access_cannot_combine_selector_and_archive_permission_across_grants() -> None:
    client, services, _ = delegated_client(
        work_grant(work_ids=["Task/a-hidden"], arena_ids=None, include_archived=False),
        work_grant(work_ids=["Task/z-visible"], arena_ids=None, include_archived=True),
    )
    seed_arena_tree(services)
    WorkObjectRepository(services.storage).archive("Task", "a-hidden")

    response = client.get("/devgraph/work/Task/a-hidden", headers=auth())

    assert response.status_code == 404


def test_anonymous_owner_local_and_missing_scope_denials_are_distinct() -> None:
    client, services, _ = delegated_client(
        ArenaReadGrant(scope="devgraph.arena.read", arena_ids=("arena-a",))
    )
    seed_arena_tree(services)

    anonymous = client.get("/devgraph/arenas")
    owner_local = client.get("/devgraph/arenas", headers=auth(FAKE_CREDENTIAL))
    missing_scope = client.get("/devgraph/work/Issue", headers=auth())

    assert anonymous.status_code == 401
    assert owner_local.status_code == 401
    assert missing_scope.status_code == 403
    for response in (anonymous, owner_local, missing_scope):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["vary"] == "Authorization, Origin"


def test_arena_members_intersect_arena_and_work_grants() -> None:
    client, services, _ = delegated_client(
        ArenaReadGrant(scope="devgraph.arena.read", arena_ids=("arena-a",)),
        work_grant(work_ids=["Initiative/root"], arena_ids=["arena-a"]),
    )
    seed_arena_tree(services)

    response = client.get("/devgraph/arenas/arena-a/members", headers=auth())

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == ["root"]


def test_graph_scope_is_all_or_nothing_and_router_has_no_mutations() -> None:
    client, services, _ = delegated_client(SimpleReadGrant(scope="devgraph.graph.read"))
    seed_arena_tree(services)

    graph = client.get("/devgraph/graph", headers=auth())
    mutation = client.post(
        "/devgraph/work/Issue",
        headers={**auth(), "Idempotency-Key": "not-authorized"},
        json={"id": "new", "title": "No"},
    )

    assert graph.status_code == 200
    assert mutation.status_code == 405
    assert services.storage.get_node("Issue", "new") is None


def test_native_owner_route_remains_compatible_but_does_not_cross_router() -> None:
    client, services, _ = delegated_client(work_grant())
    seed_arena_tree(services)

    native = client.get("/work/Issue", headers=auth(FAKE_CREDENTIAL))
    delegated = client.get("/devgraph/work/Issue", headers=auth(FAKE_CREDENTIAL))

    assert native.status_code == 200
    assert delegated.status_code == 401
