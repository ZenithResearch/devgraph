"""Wallet-signed monitor reads expose only the approved Work projection."""

import json

import pytest
from tests.auth.test_access_policy import draft
from tests.native_read.test_signed_authority import setup

from devgraph.auth.monitor_read import (
    AccessDraftV2,
    EnrollmentPolicyV2,
    MemberAccessPolicyV2,
    MonitorWorkGrant,
    project_monitor_read,
)
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Task
from devgraph.storage.memory import MemoryGraphStorage


def test_new_read_contract_explicitly_admits_all_todo_types_without_widening_v1():
    raw = {
        "scope": "devgraph.work.read",
        "operations": ["list", "get"],
        "relationship_types": ["children", "parent"],
        "work_kinds": ["Todo", "Task"],
        "include_archived": False,
    }
    grant = MonitorWorkGrant.model_validate(raw)
    assert grant.work_kinds == ("Todo", "Task")
    from devgraph.auth.delegated_contract import WorkReadGrant

    with pytest.raises(ValueError):
        WorkReadGrant.model_validate(raw)
    policy = EnrollmentPolicyV2.model_validate(
        {
            "schema": "devgraph.native-read-policy.v2",
            "issuer": "fixture",
            "receiver": "https://example.test",
            "lifetime_seconds": 600,
            "grants": [raw],
        }
    )
    assert policy.grants[0].work_kinds == ("Todo", "Task")


def test_monitor_projection_filters_items_and_edges_before_counts_or_pagination():
    storage = MemoryGraphStorage()
    repo = WorkObjectRepository(storage)
    for identifier, progress in [
        ("a-private", "not_started"),
        ("b-visible", "not_started"),
        ("c-visible", "in_progress"),
    ]:
        repo.create(Task(id=identifier, title=identifier, progress=progress, workflow_json=None))
    grant = MonitorWorkGrant.model_validate(
        {
            "scope": "devgraph.work.read",
            "operations": ["list", "get"],
            "relationship_types": ["parent"],
            "work_kinds": ["Task"],
            "work_ids": ["Task/b-visible", "Task/c-visible"],
            "include_archived": False,
        }
    )
    result = project_monitor_read(
        storage, [grant], "/todos/v2?progress=not_started&archived=exclude&limit=1"
    )
    assert result["total"] == 1
    assert [i["id"] for i in result["items"]] == ["b-visible"]
    snapshot = project_monitor_read(storage, [grant], "/monitor/topology/v1")
    assert {n["id"] for n in snapshot["graph_nodes"]} == {"b-visible", "c-visible"}
    with pytest.raises((KeyError, ValueError)):
        project_monitor_read(storage, [grant], "/todos/v2/Task/a-private")
    for path in ("/local/secrets", "/todos/v2/Task/b-visible/../../a-private", "https://evil.test"):
        with pytest.raises(ValueError):
            project_monitor_read(storage, [grant], path)


def test_relationship_authority_does_not_cross_product_with_unrelated_visible_items():
    storage = MemoryGraphStorage()
    repo = WorkObjectRepository(storage)
    for identifier in ("root", "second", "third"):
        repo.create(Task(id=identifier, title=identifier, workflow_json=None))
    storage.create_edge("Task", "second", "HAS_CHILD", "Task", "third")

    def grant(ids, operations):
        return MonitorWorkGrant.model_validate(
            {
                "scope": "devgraph.work.read",
                "operations": operations,
                "relationship_types": ["children"],
                "work_kinds": ["Task"],
                "work_ids": ids,
                "include_archived": False,
            }
        )

    grants = [
        grant(["Task/root"], ["list", "relationships"]),
        grant(["Task/second", "Task/third"], ["list"]),
    ]
    assert project_monitor_read(storage, grants, "/monitor/topology/v1")["graph_edges"] == []
    from devgraph.auth.errors import ForbiddenError

    with pytest.raises((ForbiddenError, KeyError)):
        project_monitor_read(storage, grants, "/work/Task/second/relationships/children")


def test_real_secs_monitor_enrollment_scoping_replay_and_revocation(tmp_path, monkeypatch):
    import hashlib

    from fastapi.testclient import TestClient
    from tests.native_read.test_http import sign
    from tests.native_read.test_signed_authority import NOW

    from devgraph.api.signed_read import create_signed_read_app
    from devgraph.auth.delegated_contract import canonical_json
    from devgraph.auth.enforcement import AuditLog
    from devgraph.auth.monitor_read import MemberMonitorReadEnrollment
    from devgraph.auth.signed_read import SecsReadAuthority
    from devgraph.model.work import Project, Todo

    monkeypatch.setattr("devgraph.auth.native_read._seconds", lambda _: int(NOW.timestamp()))
    monkeypatch.setattr("devgraph.auth.signed_read._seconds", lambda _: int(NOW.timestamp()))
    old, owner, _, policies, _ = setup(tmp_path)
    public = owner.public_key().public_bytes_raw().hex()
    raw = {
        "scope": "devgraph.work.read",
        "operations": ["list", "get", "relationships"],
        "relationship_types": ["children", "parent"],
        "work_kinds": ["Todo", "Project", "Task"],
        "include_archived": False,
    }
    policy = EnrollmentPolicyV2.model_validate(
        {
            "schema": "devgraph.native-read-policy.v2",
            "issuer": policies[0].issuer,
            "receiver": policies[0].receiver,
            "lifetime_seconds": 600,
            "grants": [raw],
        }
    )
    authority = old.authority
    pinned = authority.policy()
    authority.policy_file.write_text(
        json.dumps(
            SecsReadAuthority.policy_for(
                policy, key_id=pinned["key_id"], public_key=pinned["public_key"]
            )
        )
    )
    root = AccessDraftV2.model_validate(
        {
            **draft().model_dump(mode="json", by_alias=True, exclude_defaults=True),
            "schema": "devgraph.access-draft.v2",
            "issuer": policy.issuer,
            "receiver": policy.receiver,
            "not_before": int(NOW.timestamp()) - 1,
            "not_after": int(NOW.timestamp()) + 3600,
            "lifetime_seconds": 600,
            "read_grants": [raw],
            "write_grants": [],
        }
    )
    selection = {
        **root.model_dump(mode="json", by_alias=True, exclude_defaults=True),
        "subject_public_key": public,
        "installation_id": "a" * 32,
        "read_grants": [{**raw, "arena_ids": ["zenith"]}],
    }
    assignment = AccessDraftV2.model_validate(selection)
    registry = [
        MemberAccessPolicyV2.model_validate(
            {
                "schema": "devgraph.member-access-policy.v2",
                "ceiling": root,
                "assignments": [assignment],
            }
        )
    ]
    service = MemberMonitorReadEnrollment(
        path=tmp_path / "monitor.sqlite",
        policy=lambda: policy,
        authority=authority,
        membership_check=old.membership_check,
        members=lambda: registry[0],
    )
    with old._database() as db:
        membership = json.loads(db.execute("SELECT proof FROM credentials").fetchone()[0])
    storage = MemoryGraphStorage()
    repo = WorkObjectRepository(storage)
    for item in [
        Project(id="root", title="Shared project"),
        Todo(id="b-visible", title="Base Todo", progress="not_started"),
        Todo(id="a-private", title="Private Todo", progress="not_started"),
        Task(id="c-child", title="Child", progress="not_started"),
    ]:
        repo.create(item)
    storage.create_node("Arena", "zenith", {"kind": "Arena"})
    storage.create_edge("Arena", "zenith", "CONTAINS_WORK", "Project", "root")
    storage.create_edge("Project", "root", "HAS_CHILD", "Todo", "b-visible")
    storage.create_edge("Project", "root", "HAS_CHILD", "Task", "c-child")
    http = TestClient(
        create_signed_read_app(storage=storage, enrollment=service, audit_log=AuditLog())
    )
    base = "/monitor/wallet-access/v1"
    assert http.get("/monitor").status_code == 200
    assert "Read access key" not in http.get("/monitor").text
    config = http.get(base).json()
    assert config["provider_profile"]["membership"] is None
    challenge = http.post(
        base + "/challenges", json={"subject_public_key": public, "installation_id": "a" * 32}
    ).json()
    enrollment = http.post(
        base + "/enrollments",
        json={"challenge": challenge, "proof": membership, "signature": sign(owner, challenge)},
    )
    assert enrollment.status_code == 200, enrollment.text
    credential = enrollment.json()["credential"]
    counter = 0

    def read(path, operation="devgraph.work.list"):
        nonlocal counter
        counter += 1
        call = {
            "operation": operation,
            "resource": "https://work.zenith-research.ca/devgraph",
            "parameters": {"path": path},
        }
        proof = {
            "schema": "castalia.read-presentation.v1",
            "credential_digest": hashlib.sha256(canonical_json(credential)).hexdigest(),
            "call_digest": hashlib.sha256(canonical_json(call)).hexdigest(),
            "receiver": policy.receiver,
            "nonce": f"{counter:032x}",
            "issued_at": int(NOW.timestamp()),
        }
        proof["signature"] = owner.sign(
            b"castalia.read-presentation.v1/signature\0" + canonical_json(proof)
        ).hex()
        return http.post(
            base + "/requests", json={"credential": credential, "call": call, "proof": proof}
        )

    page = read("/todos/v2?progress=not_started&limit=1")
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 3
    assert len(page.json()["items"]) == 1
    assert page.json()["items"][0]["id"] == "root"
    assert read("/todos/v2/Todo/a-private", "devgraph.work.get").status_code == 404
    assert read("/work-operations/v1", "devgraph.work.create.v2").status_code == 401
    topology = read("/monitor/topology/v1").json()
    assert {n["id"] for n in topology["graph_nodes"]} == {"root", "b-visible", "c-child"}
    assert topology["access_scope"]["observations"] is False
    registry[0] = MemberAccessPolicyV2.model_validate(
        {
            "schema": "devgraph.member-access-policy.v2",
            "revision": 2,
            "ceiling": root,
            "assignments": [],
        }
    )
    assert read("/todos/v2").status_code == 401


@pytest.mark.parametrize("kind", ["Todo", "Proposal", "Initiative", "Project", "Issue", "Task"])
def test_each_todo_kind_has_a_scoped_reader(kind):
    from devgraph.model import work

    storage = MemoryGraphStorage()
    WorkObjectRepository(storage).create(getattr(work, kind)(id="visible", title="Visible"))
    WorkObjectRepository(storage).create(getattr(work, kind)(id="hidden", title="Hidden"))
    grant = MonitorWorkGrant.model_validate(
        {
            "scope": "devgraph.work.read",
            "operations": ["get"],
            "relationship_types": ["parent"],
            "work_kinds": [kind],
            "work_ids": [kind + "/visible"],
            "include_archived": False,
        }
    )
    result = project_monitor_read(storage, [grant], f"/todos/v2/{kind}/visible")
    assert result["id"] == "visible"
    with pytest.raises(KeyError):
        project_monitor_read(storage, [grant], f"/todos/v2/{kind}/hidden")


def test_modern_drafts_attenuate_without_accepting_implicit_contract_upgrade(tmp_path):
    from devgraph.auth.access_policy import require_subset
    from devgraph.ops.access_plans import _draft, _matching

    root = AccessDraftV2.model_validate(
        {
            **draft().model_dump(mode="json", by_alias=True, exclude_defaults=True),
            "schema": "devgraph.access-draft.v2",
            "read_grants": [
                {
                    "scope": "devgraph.work.read",
                    "operations": ["list", "get"],
                    "relationship_types": ["parent"],
                    "work_kinds": ["Todo", "Task"],
                    "include_archived": True,
                }
            ],
        }
    )
    child = _draft(
        {
            **root.model_dump(mode="json", by_alias=True, exclude_defaults=True),
            "subject_public_key": "a" * 64,
            "installation_id": "b" * 32,
            "read_grants": [
                {
                    "scope": "devgraph.work.read",
                    "operations": ["list"],
                    "relationship_types": ["parent"],
                    "work_kinds": ["Todo"],
                    "arena_ids": ["zenith"],
                    "include_archived": False,
                }
            ],
        }
    )
    require_subset(child, root)
    with pytest.raises(ValueError, match="contract"):
        _matching(child, draft())
    enlarged = child.model_copy(update={"read_grants": root.read_grants})
    with pytest.raises(ValueError, match="amplification"):
        require_subset(enlarged, child)


@pytest.mark.parametrize(
    "relationship,edge,incoming",
    [
        ("children", "HAS_CHILD", False),
        ("parent", "HAS_CHILD", True),
        ("dependencies", "DEPENDS_ON", False),
        ("dependents", "DEPENDS_ON", True),
        ("blockers", "BLOCKS", True),
        ("blocked", "BLOCKS", False),
    ],
)
def test_related_reader_uses_the_canonical_edge_direction(relationship, edge, incoming):
    storage = MemoryGraphStorage()
    repo = WorkObjectRepository(storage)
    for identifier in ("anchor", "related"):
        repo.create(Task(id=identifier, title=identifier))
    source, target = ("related", "anchor") if incoming else ("anchor", "related")
    storage.create_edge("Task", source, edge, "Task", target)
    grant = MonitorWorkGrant.model_validate(
        {
            "scope": "devgraph.work.read",
            "operations": ["relationships"],
            "relationship_types": [relationship],
            "work_kinds": ["Task"],
            "include_archived": False,
        }
    )
    result = project_monitor_read(
        storage, [grant], f"/work/Task/anchor/relationships/{relationship}"
    )
    assert [item["id"] for item in result["items"]] == ["related"]


def test_blocker_permission_on_an_anchor_does_not_reveal_its_outgoing_blocked_items():
    storage = MemoryGraphStorage()
    repo = WorkObjectRepository(storage)
    for identifier in ("anchor", "other"):
        repo.create(Task(id=identifier, title=identifier))

    def grant(ids, operations, relations):
        return MonitorWorkGrant.model_validate(
            {
                "scope": "devgraph.work.read",
                "operations": operations,
                "relationship_types": relations,
                "work_kinds": ["Task"],
                "work_ids": ids,
                "include_archived": False,
            }
        )

    grants = [
        grant(["Task/anchor"], ["list", "relationships"], ["blockers"]),
        grant(["Task/other"], ["list"], ["parent"]),
    ]
    storage.create_edge("Task", "anchor", "BLOCKS", "Task", "other")
    assert project_monitor_read(storage, grants, "/monitor/topology/v1")["graph_edges"] == []
    storage.create_edge("Task", "other", "BLOCKS", "Task", "anchor")
    assert len(project_monitor_read(storage, grants, "/monitor/topology/v1")["graph_edges"]) == 1
