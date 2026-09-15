from __future__ import annotations

import json

import pytest

from devgraph.work_requests import InvalidWorkRequest, WorkRequest


def request(operation="create", kind="Issue", work_id="i-1", version=None, payload=None):
    return {
        "schema": "devgraph.work-request.v1",
        "operation": operation,
        "kind": kind,
        "id": work_id,
        "expected_version": version,
        "payload": {"id": work_id, "title": "Example"} if payload is None else payload,
    }


def parse(value):
    return WorkRequest.from_json(json.dumps(value).encode())


@pytest.mark.parametrize("kind", ["Proposal", "Initiative", "Project", "Issue", "Task"])
def test_create_materializes_defaults_and_freezes_signed_snapshot(kind):
    command = parse(request(kind=kind))
    explicit = request(kind=kind)
    explicit["payload"].update(description="", priority=0, artifact_ids=[], external_link_ids=[])
    assert parse(explicit).canonical == command.canonical
    assert parse(explicit).digest == command.digest
    command.payload["title"] = "Cannot alter the signed request"
    assert command.payload["title"] == "Example"
    assert command.resources == (f"{kind}/i-1",)


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation", "cypher"),
        ("kind", "Decision"),
        ("id", "bad_id"),
        ("expected_version", 1),
        ("schema", "devgraph.issue.create.v1"),
    ],
)
def test_invalid_envelope_does_not_enter_the_contract(field, value):
    body = request()
    body[field] = value
    with pytest.raises(InvalidWorkRequest, match="^invalid_work_request$"):
        parse(body)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema":"devgraph.work-request.v1","schema":"devgraph.work-request.v1"}',
        b'{"payload":{"priority":1.0}}',
        b'{"payload":{"priority":9007199254740992}}',
        b" " * 131_073,
    ],
)
def test_ambiguous_or_oversized_json_rejected(raw):
    with pytest.raises(InvalidWorkRequest):
        WorkRequest.from_json(raw)


def test_patch_binds_version_and_preserves_omission_vs_empty():
    base = request("patch", version=2, payload={"description": ""})
    command = parse(base)
    assert command.payload == {"description": ""}
    assert command.digest != parse({**base, "expected_version": 3}).digest
    with pytest.raises(InvalidWorkRequest):
        parse({**base, "expected_version": None})
    with pytest.raises(InvalidWorkRequest):
        parse({**base, "payload": {"description": None}})


def test_reparent_requires_authority_for_both_parents():
    body = request(
        "parent.set",
        version=2,
        payload={
            "previous_parent": {"kind": "Project", "id": "p-old", "expected_version": 3},
            "parent": {"kind": "Project", "id": "p-new", "expected_version": 4},
        },
    )
    assert parse(body).resources == ("Issue/i-1", "Project/p-new", "Project/p-old")
    body["payload"]["parent"]["kind"] = "Initiative"
    with pytest.raises(InvalidWorkRequest):
        parse(body)


@pytest.mark.parametrize(
    "operation,payload,resources",
    [
        (
            "accept",
            {"decision_id": "d-1", "decision_title": "Accept"},
            ("Decision/d-1", "Proposal/p-1"),
        ),
        (
            "convert",
            {"issue_id": "i-new", "decision_id": "d-1"},
            ("Decision/d-1", "Issue/i-new", "Proposal/p-1"),
        ),
    ],
)
def test_proposal_operations_bind_destination(operation, payload, resources):
    assert parse(request(operation, "Proposal", "p-1", 2, payload)).resources == resources


def test_dependency_direction_and_both_versions_are_bound():
    body = request(
        "dependency.add",
        version=2,
        payload={
            "target": {"kind": "Project", "id": "p-1", "expected_version": 3},
        },
    )
    original = parse(body)
    assert original.resources == ("Issue/i-1", "Project/p-1")
    body["payload"]["target"]["expected_version"] = 4
    assert original.digest != parse(body).digest
    body["payload"]["target"].update(kind="Issue", id="i-1")
    with pytest.raises(InvalidWorkRequest):
        parse(body)


def test_blockers_are_tasks_only():
    body = request(
        "blocker.add",
        version=2,
        payload={
            "target": {"kind": "Task", "id": "t-1", "expected_version": 3},
        },
    )
    with pytest.raises(InvalidWorkRequest):
        parse(body)
