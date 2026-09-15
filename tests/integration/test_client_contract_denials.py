from __future__ import annotations

import json
import socket
import subprocess
from typing import Any

import pytest
from client_contract_fixtures import ClientContractFixture, build_client_contract_fixture

from devgraph.client import DevgraphProblem, DevgraphRequestContext
from devgraph.events.outbox import EMITTED_EVENT, EVENT_RECEIPT_LABEL
from devgraph.model.work import Issue


class MissingCredentialTransport:
    def __init__(self, transport) -> None:
        self._transport = transport

    def request(self, method: str, url: str, **kwargs: Any):
        headers = dict(kwargs.get("headers", {}))
        headers.pop("Authorization", None)
        kwargs["headers"] = headers
        return self._transport.request(method, url, **kwargs)


def context_for(fixture: ClientContractFixture, name: str) -> DevgraphRequestContext:
    if name == "missing":
        return DevgraphRequestContext(credential="removed-before-http")
    return getattr(fixture.contexts, name)


def invoke_route(
    fixture: ClientContractFixture,
    route: str,
    context: DevgraphRequestContext,
) -> None:
    client = fixture.client
    if route == "create":
        client.create_issue(
            context,
            work_id="denied-issue",
            title="denied-payload-marker",
            idempotency_key="denied-idempotency-marker",
        )
    elif route == "get":
        client.get_issue(context, work_id="denied-issue")
    elif route == "list":
        client.list_issues(context, include_archived=False)
    else:
        client.transition_issue_to_review(
            context,
            work_id="denied-issue",
            idempotency_key="denied-idempotency-marker",
        )


def invoke_generic_route(
    fixture: ClientContractFixture,
    route: str,
    context: DevgraphRequestContext,
) -> None:
    client = fixture.client
    if route == "create":
        client.create_work(
            context,
            kind="Project",
            work_id="denied-project",
            title="denied-payload-marker",
            idempotency_key="denied-idempotency-marker",
        )
    elif route == "get":
        client.get_work(context, kind="Project", work_id="denied-project")
    elif route == "list":
        client.list_work(context, kind="Project")
    elif route == "patch":
        client.patch_work(
            context,
            kind="Project",
            work_id="denied-project",
            expected_version=1,
            title="denied-payload-marker",
            idempotency_key="denied-idempotency-marker",
        )
    elif route == "status":
        client.transition_work_status(
            context,
            kind="Project",
            work_id="denied-project",
            status="review",
            idempotency_key="denied-idempotency-marker",
        )
    elif route == "archive":
        client.archive_work(
            context,
            kind="Project",
            work_id="denied-project",
            idempotency_key="denied-idempotency-marker",
        )
    elif route == "children":
        client.get_work_children(context, kind="Project", work_id="denied-project")
    elif route == "blockers":
        client.get_task_blockers(context, task_id="denied-task")
    elif route == "accept":
        client.accept_proposal(
            context,
            proposal_id="denied-proposal",
            decision_id="denied-decision",
            decision_title="denied-payload-marker",
            idempotency_key="denied-idempotency-marker",
        )
    else:
        client.convert_proposal(
            context,
            proposal_id="denied-proposal",
            issue_id="denied-issue",
            idempotency_key="denied-idempotency-marker",
        )


DENIAL_CASES = [
    ("create", "read_only", 403),
    ("create", "wrong_scope", 403),
    ("create", "invalid", 401),
    ("create", "missing", 401),
    ("transition", "read_only", 403),
    ("transition", "wrong_scope", 403),
    ("transition", "invalid", 401),
    ("transition", "missing", 401),
    ("get", "write_only", 403),
    ("get", "wrong_scope", 403),
    ("get", "invalid", 401),
    ("get", "missing", 401),
    ("list", "write_only", 403),
    ("list", "wrong_scope", 403),
    ("list", "invalid", 401),
    ("list", "missing", 401),
]

_READ_ROUTES = ("get", "list", "children", "blockers")
_WRITE_ROUTES = ("create", "patch", "status", "archive", "accept", "convert")
GENERIC_DENIAL_CASES = [
    *(
        (route, context_name, status)
        for route in _READ_ROUTES
        for context_name, status in (
            ("write_only", 403),
            ("wrong_scope", 403),
            ("invalid", 401),
            ("missing", 401),
        )
    ),
    *(
        (route, context_name, status)
        for route in _WRITE_ROUTES
        for context_name, status in (
            ("read_only", 403),
            ("wrong_scope", 403),
            ("invalid", 401),
            ("missing", 401),
        )
    ),
]


@pytest.mark.parametrize(("route", "context_name", "status"), DENIAL_CASES)
def test_exact_denial_matrix_has_safe_problem_and_zero_effects(
    route: str, context_name: str, status: int
) -> None:
    fixture = build_client_contract_fixture()
    fixture.observation.repository.create(Issue(id="denied-issue", title="seed"))
    if context_name == "missing":
        fixture.client._transport = MissingCredentialTransport(fixture.transport)
    before_nodes = fixture.observation.storage.query()
    before_edges = fixture.observation.storage.list_edges()
    fixture.observation.repository.reset_calls()
    context = context_for(fixture, context_name)

    with pytest.raises(DevgraphProblem) as captured:
        invoke_route(fixture, route, context)

    problem = captured.value
    assert problem.status == status
    assert problem.title == ("Unauthenticated" if status == 401 else "Forbidden")
    assert fixture.observation.storage.query() == before_nodes
    assert fixture.observation.storage.list_edges() == before_edges
    assert fixture.observation.storage.query(EVENT_RECEIPT_LABEL) == []
    assert fixture.observation.storage.list_edges(EMITTED_EVENT) == []
    assert fixture.observation.audit_log.records == []
    assert fixture.observation.repository.calls == {
        "get_by_id": 0,
        "query": 0,
        "create": 0,
        "transition_status": 0,
    }
    rendered = json.dumps(vars(problem)) + str(problem) + repr(problem)
    assert context.credential not in rendered
    assert "denied-idempotency-marker" not in rendered
    assert "denied-payload-marker" not in rendered


@pytest.mark.parametrize(
    ("route", "context_name"),
    [
        ("create", "write_only"),
        ("transition", "write_only"),
        ("get", "read_only"),
        ("list", "read_only"),
    ],
)
def test_each_route_accepts_only_its_exact_required_scope(route: str, context_name: str) -> None:
    fixture = build_client_contract_fixture()
    fixture.observation.repository.create(Issue(id="denied-issue", title="seed"))
    if route == "create":
        fixture = build_client_contract_fixture()
    invoke_route(fixture, route, context_for(fixture, context_name))


@pytest.mark.parametrize(("route", "context_name", "status"), GENERIC_DENIAL_CASES)
def test_generic_route_denials_are_safe_and_have_zero_effects(
    route: str,
    context_name: str,
    status: int,
) -> None:
    fixture = build_client_contract_fixture()
    if context_name == "missing":
        fixture.client._transport = MissingCredentialTransport(fixture.transport)
    before_nodes = fixture.observation.storage.query()
    before_edges = fixture.observation.storage.list_edges()
    fixture.observation.repository.reset_calls()
    context = context_for(fixture, context_name)

    with pytest.raises(DevgraphProblem) as captured:
        invoke_generic_route(fixture, route, context)

    problem = captured.value
    assert problem.status == status
    assert problem.title == ("Unauthenticated" if status == 401 else "Forbidden")
    assert fixture.observation.storage.query() == before_nodes
    assert fixture.observation.storage.list_edges() == before_edges
    assert fixture.observation.storage.query(EVENT_RECEIPT_LABEL) == []
    assert fixture.observation.storage.list_edges(EMITTED_EVENT) == []
    assert fixture.observation.audit_log.records == []
    assert fixture.observation.repository.calls == {
        "get_by_id": 0,
        "query": 0,
        "create": 0,
        "transition_status": 0,
    }
    rendered = json.dumps(vars(problem)) + str(problem) + repr(problem)
    assert context.credential not in rendered
    assert "denied-idempotency-marker" not in rendered
    assert "denied-payload-marker" not in rendered


def test_proof_invokes_no_process_listener_or_external_network(monkeypatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("process, listener, or external network operation attempted")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket.socket, "bind", forbidden)
    monkeypatch.setattr(socket.socket, "listen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)

    fixture = build_client_contract_fixture()
    fixture.client.create_issue(
        fixture.contexts.full_sequence,
        work_id="instrumented-issue",
        title="instrumented",
        idempotency_key="instrumented-create",
    )
    fixture.client.get_issue(fixture.contexts.full_sequence, work_id="instrumented-issue")


def test_denial_parameterization_covers_every_route_and_fixture() -> None:
    expected: dict[str, set[str]] = {
        "create": {"read_only", "wrong_scope", "invalid", "missing"},
        "transition": {"read_only", "wrong_scope", "invalid", "missing"},
        "get": {"write_only", "wrong_scope", "invalid", "missing"},
        "list": {"write_only", "wrong_scope", "invalid", "missing"},
    }
    actual: dict[str, set[str]] = {route: set() for route in expected}
    for route, context_name, _ in DENIAL_CASES:
        actual[route].add(context_name)
    assert actual == expected


def test_generic_denial_parameterization_covers_every_bounded_route() -> None:
    assert {route for route, _, _ in GENERIC_DENIAL_CASES} == {
        "create",
        "get",
        "list",
        "patch",
        "status",
        "archive",
        "children",
        "blockers",
        "accept",
        "convert",
    }
    assert len(GENERIC_DENIAL_CASES) == 40
