"""Real native session verification, then selector-intersected aggregate reads."""

import copy
import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.auth.test_access_policy import draft
from tests.model.test_arena_runtime import add_arena
from tests.native_read.test_http import sign
from tests.native_read.test_signed_authority import NOW, setup

from devgraph.arenas import ArenaMutations
from devgraph.auth.arena_access import ArenaAccessDraft, ArenaMemberPolicy, compile_arena_read
from devgraph.auth.arena_activation import ArenaMonitorReadEnrollment
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.holder_grants import HolderGrantHost, checksum
from devgraph.auth.holder_sessions import HolderSessionHost, HolderSessionPolicy
from devgraph.auth.monitor_read import MemberMonitorReadEnrollment, project_monitor_read
from devgraph.auth.selector_grants import materialize
from devgraph.auth.signed_read import SecsReadAuthority
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Initiative, Project
from devgraph.storage.memory import MemoryGraphStorage


class FixtureEnrollment(MemberMonitorReadEnrollment):
    """Static synthetic assignments; production Arena decoding, no provisioning UI."""

    _decode_enrollment_ceiling = ArenaMonitorReadEnrollment._decode_enrollment_ceiling
    _decode_grants = ArenaMonitorReadEnrollment._decode_grants
    _policy_for = ArenaMonitorReadEnrollment._policy_for


def fixture(tmp_path):
    binary = os.environ.get("SECS_HOLDER_GRANT_TEST_BINARY")
    if not binary:
        pytest.skip("actual native holder authority required")
    old, owner, _, policies, _ = setup(tmp_path)
    current = int(NOW.timestamp())
    raw = draft(
        issuer=policies[0].issuer,
        receiver=policies[0].receiver,
        not_before=current - 1,
        not_after=current + 3600,
        lifetime_seconds=600,
        write_grants=[],
    ).model_dump(mode="json", by_alias=True, exclude_defaults=True)
    raw.update(
        schema="devgraph.access-draft.v6",
        read_grants=[
            dict(
                scope="devgraph.work.read",
                operations=["list", "get", "relationships"],
                relationship_types=["children", "parent"],
                work_kinds=["Initiative", "Project"],
                include_archived=False,
            ),
            dict(scope="devgraph.arena.read", arena_ids=["shared"]),
        ],
    )
    raw["validity"] = dict(
        schema="castalia.grant-validity.v1",
        mode="dated",
        not_before=raw.pop("not_before"),
        not_after=raw.pop("not_after"),
    )
    ceiling = ArenaAccessDraft.model_validate(raw)
    assignment = ceiling.model_copy(
        update={
            "subject_public_key": owner.public_key().public_bytes_raw().hex(),
            "installation_id": "a" * 32,
        }
    )
    registry = [
        ArenaMemberPolicy.model_validate(
            dict(
                schema="devgraph.member-access-policy.v5",
                ceiling=ceiling,
                assignments=[assignment],
            )
        )
    ]
    policy = compile_arena_read(ceiling)
    pinned = old.authority.policy()
    old.authority.policy_file.write_text(
        json.dumps(
            SecsReadAuthority.policy_for(
                policy,
                key_id=pinned["key_id"],
                public_key=pinned["public_key"],
            )
        )
    )
    enrollment = FixtureEnrollment(
        path=tmp_path / "aggregate.sqlite",
        policy=lambda: policy,
        authority=old.authority,
        membership_check=lambda p, _: p == {},
        members=lambda: registry[0],
    )
    host = HolderGrantHost(
        enrollment=enrollment,
        binary=Path(binary),
        binary_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        service="fixture:aggregate",
        grant_schema="secs.holder-grant.v2",
    )
    challenge = enrollment.challenge(assignment.subject_public_key, "a" * 32, now=NOW)
    parent = host.issue(challenge=challenge, proof={}, signature=sign(owner, challenge), now=NOW)[
        "grant"
    ]
    session = HolderSessionHost(
        host, HolderSessionPolicy(enabled=True, allowed_origins=["https://graph.example"])
    )
    key = Ed25519PrivateKey.generate()
    prepared = session.prepare(
        dict(
            schema="secs.holder-session-prepare.v1",
            request_id="44" * 16,
            parent_grant=parent,
            installation_id="a" * 32,
            session_installation_id="b" * 32,
            session_public_key=key.public_key().public_bytes_raw().hex(),
            origin="https://graph.example",
            audience="devgraph",
            requested_ttl_seconds=300,
            permissions=None,
        ),
        now=NOW,
    )["challenge"]
    auth = dict(
        schema="secs.holder-session-authorization.v1",
        challenge=prepared,
        parent_grant=parent,
        holder_signature=owner.sign(
            b"secs.holder-session-authorization.v1/signature\0" + canonical_json(prepared)
        ).hex(),
        session_signature=key.sign(
            b"secs.holder-session-possession.v1/signature\0" + canonical_json(prepared)
        ).hex(),
    )
    token = session.exchange(auth, now=NOW)["credential"]
    storage = MemoryGraphStorage()
    add_arena(ArenaMutations(storage), "shared")
    add_arena(ArenaMutations(storage), "private")
    repo = WorkObjectRepository(storage)
    repo.create(Initiative(id="parent", title="Parent"))
    repo.create(Project(id="child", title="Child"))
    storage.create_edge("Arena", "shared", "CONTAINS_WORK", "Initiative", "parent")
    storage.create_edge("Initiative", "parent", "HAS_CHILD", "Project", "child")
    return session, enrollment, key, token, storage


def presentation(key, token, path="/monitor/topology/v1", nonce="88" * 16):
    call = dict(
        operation="devgraph.work.list",
        resource="https://work.zenith-research.ca/devgraph",
        parameters={"path": path},
    )
    claims = dict(
        schema="secs.holder-session-presentation.v1",
        credential_digest=checksum(token),
        call_digest=checksum(call),
        nonce=nonce,
        issued_at=int(NOW.timestamp()),
        expires_at=int(NOW.timestamp()) + 60,
    )
    return dict(
        credential=token,
        call=call,
        proof=dict(
            claims,
            signature=key.sign(
                b"secs.holder-session-presentation.v1/signature\0" + canonical_json(claims)
            ).hex(),
        ),
    )


def test_native_composite_session_discloses_only_signed_arena_and_real_hierarchy(tmp_path):
    s, e, key, token, storage = fixture(tmp_path)
    assert "devgraph.work.relationships" in {
        p["operation"] for p in token["claims"]["challenge"]["permissions"]
    }
    request = presentation(key, token)
    with s.from_origin("https://graph.example"):
        context = e.verify_request(**request, now=NOW)
        with (
            storage.work_mutation_transaction(),
            s.host.current_read(token, request["proof"], now=NOW),
        ):
            result = project_monitor_read(
                storage,
                materialize(storage, context.read_grants),
                request["call"]["parameters"]["path"],
            )
        assert [a["id"] for a in result["arenas"]] == ["shared"]
        assert {edge["relationship"] for edge in result["graph_edges"]} == {
            "CONTAINS_WORK",
            "HAS_CHILD",
        }
        assert "private" not in json.dumps(result)
        conditional_path = "/monitor/topology/v1?if_revision=" + result["representation_revision"]
        conditional = presentation(key, token, conditional_path, nonce="cd" * 16)
        checked = e.verify_request(**conditional, now=NOW)
        with (
            storage.work_mutation_transaction(),
            s.host.current_read(token, conditional["proof"], now=NOW),
        ):
            unchanged = project_monitor_read(
                storage, materialize(storage, checked.read_grants), conditional_path
            )
        assert unchanged["schema"] == "devgraph.topology-not-modified.v1"
        with pytest.raises(UnauthenticatedError, match="replay"):
            e.verify_request(**request, now=NOW)
        with pytest.raises(UnauthenticatedError):
            e.verify_request(
                **presentation(key, token, nonce="99" * 16), now=NOW + timedelta(seconds=301)
            )
        e.revoke("dgrg_" + token["claims"]["challenge"]["parent_grant_id"])
        with pytest.raises(UnauthenticatedError):
            e.verify_request(**presentation(key, token, conditional_path, nonce="aa" * 16), now=NOW)


def test_ordinary_read_retains_matching_operation_and_tampered_inventory_denies(tmp_path):
    s, e, key, token, _ = fixture(tmp_path)
    with s.from_origin("https://graph.example"):
        context = e.verify_request(**presentation(key, token, "/todos/v2"), now=NOW)
        assert all(
            g.scope == "devgraph.work.read" and g.operations == ("list",)
            for g in context.read_grants
        )
        changed = copy.deepcopy(token)
        changed["claims"]["challenge"]["permissions"][-1]["constraints"]["arena_ids"] = ["private"]
        with pytest.raises(UnauthenticatedError):
            e.verify_request(**presentation(key, changed, nonce="ab" * 16), now=NOW)


def test_http_conditional_read_rechecks_native_session_after_revocation(tmp_path, monkeypatch):
    from importlib import import_module

    from fastapi.testclient import TestClient

    from devgraph.api.signed_read import create_signed_read_app
    from devgraph.auth.enforcement import AuditLog

    s, e, key, token, storage = fixture(tmp_path)
    for name in ("native_read", "signed_read", "holder_grants", "holder_sessions"):
        monkeypatch.setattr(
            import_module("devgraph.auth." + name), "_seconds", lambda _: int(NOW.timestamp())
        )
    app = create_signed_read_app(storage=storage, enrollment=e, audit_log=AuditLog())
    with TestClient(app) as client:
        endpoint = "/monitor/wallet-access/v1/requests"
        headers = {"Origin": "https://graph.example"}
        full = client.post(endpoint, json=presentation(key, token), headers=headers)
        assert full.status_code == 200
        assert {x["relationship"] for x in full.json()["graph_edges"]} == {
            "HAS_CHILD",
            "CONTAINS_WORK",
        }
        target = "/monitor/topology/v1?if_revision=" + full.json()["representation_revision"]
        small = client.post(
            endpoint, json=presentation(key, token, target, "cd" * 16), headers=headers
        )
        assert small.status_code == 200
        assert small.json()["schema"] == "devgraph.topology-not-modified.v1"
        e.revoke("dgrg_" + token["claims"]["challenge"]["parent_grant_id"])
        denied = client.post(
            endpoint, json=presentation(key, token, target, "de" * 16), headers=headers
        )
        assert denied.status_code == 401
        assert "representation_revision" not in denied.json()
