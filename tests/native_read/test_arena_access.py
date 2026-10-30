"""Arena metadata permission is independent of Work Arena selection."""

import json

import pytest
from tests.native_read.test_path_access_flow import setup
from tests.native_read.test_standing_access import standing

from devgraph.auth.access_policy import require_subset
from devgraph.auth.arena_access import ArenaAccessDraft, compile_arena_read
from devgraph.auth.delegated_contract import ArenaReadGrant, canonical_json
from devgraph.auth.signed_read import SecsReadAuthority
from devgraph.auth.standing_access import StandingAccessDraft


def arena_draft(tmp_path):
    old = standing(setup(tmp_path).base[0].ceiling)
    raw = json.loads(canonical_json(old))
    raw["schema"] = "devgraph.access-draft.v6"
    raw["read_grants"].append({"scope": "devgraph.arena.read", "arena_ids": ["arena-one"]})
    return ArenaAccessDraft.model_validate(raw)


def test_new_wire_and_projection_keep_old_contracts_closed(tmp_path):
    draft = arena_draft(tmp_path)
    policy = compile_arena_read(draft)
    assert isinstance(policy.grants[-1], ArenaReadGrant)
    assert policy.schema_ == "devgraph.native-read-policy.v4"
    with pytest.raises(ValueError):
        StandingAccessDraft.model_validate_json(canonical_json(draft))
    projected = SecsReadAuthority.policy_for(policy, key_id="fixture", public_key="ab" * 32)
    arena = [p for p in projected["permissions"] if p["operation"].startswith("devgraph.arena.")]
    assert {p["operation"] for p in arena} == {"devgraph.arena.list", "devgraph.arena.get"}
    assert all(
        p["constraints"] == {"scope": "devgraph.arena.read", "arena_ids": ["arena-one"]}
        for p in arena
    )
    raw = json.loads(canonical_json(policy))
    raw["schema"] = "devgraph.native-read-policy.v3"
    from devgraph.auth.path_access import PathEnrollmentPolicy

    with pytest.raises(ValueError):
        PathEnrollmentPolicy.model_validate(raw)


def test_work_arena_selector_cannot_authorize_arena_metadata(tmp_path):
    parent = arena_draft(tmp_path)
    raw = json.loads(canonical_json(parent))
    raw["read_grants"] = [g for g in raw["read_grants"] if g["scope"] == "devgraph.work.read"]
    work_only = ArenaAccessDraft.model_validate(raw)
    with pytest.raises(ValueError, match="read grant amplification"):
        require_subset(parent, work_only)
    raw = json.loads(canonical_json(parent))
    raw["read_grants"][-1]["arena_ids"] = ["another-arena"]
    with pytest.raises(ValueError, match="read grant amplification"):
        require_subset(ArenaAccessDraft.model_validate(raw), parent)
    require_subset(work_only, parent)
    for ids in ([], ["arena-one", "arena-one"]):
        with pytest.raises(ValueError):
            ArenaReadGrant(scope="devgraph.arena.read", arena_ids=ids)


def test_arena_projection_intersects_work_grants_and_rejects_unlisted_metadata():
    from tests.model.test_arena_runtime import add_arena, assign
    from tests.model.test_named_work import create, ref

    from devgraph.arenas import ArenaMutations
    from devgraph.auth.errors import ForbiddenError
    from devgraph.auth.monitor_read import MonitorWorkGrant, project_monitor_read
    from devgraph.named_work import NamedWorkMutations
    from devgraph.storage.memory import MemoryGraphStorage

    storage = MemoryGraphStorage()
    arenas = ArenaMutations(storage)
    add_arena(arenas, "arena-one")
    add_arena(arenas, "private-arena")
    work = NamedWorkMutations(storage)
    for index, name in enumerate(("visible", "private"), 1):
        create(work, "Task", name)
        assign(arenas, "Task", name, 1, arena=ref("Arena", "arena-one", index))
    metadata = ArenaReadGrant(scope="devgraph.arena.read", arena_ids=["arena-one"])
    grant = MonitorWorkGrant(
        scope="devgraph.work.read",
        operations=["list", "get"],
        work_kinds=["Task"],
        work_ids=["Task/visible"],
        relationship_types=["parent"],
        include_archived=False,
    )
    assert [a["id"] for a in project_monitor_read(storage, [metadata], "/arenas")["items"]] == [
        "arena-one"
    ]
    assert project_monitor_read(storage, [metadata], "/arenas/arena-one")["id"] == "arena-one"
    with pytest.raises(KeyError):
        project_monitor_read(storage, [metadata], "/arenas/private-arena")
    with pytest.raises(ForbiddenError):
        project_monitor_read(storage, [grant], "/arenas")
    with pytest.raises(ForbiddenError):
        project_monitor_read(storage, [metadata], "/arenas/arena-one/members")
    assert [
        t["id"]
        for t in project_monitor_read(storage, [metadata, grant], "/arenas/arena-one/members")[
            "items"
        ]
    ] == ["visible"]
    assert [
        t["id"] for t in project_monitor_read(storage, [metadata, grant], "/todos/v2")["items"]
    ] == ["visible"]
    for target in (
        "/arenas?limit=101",
        "/arenas?include_archived=true",
        "/arenas/arena-one?limit=1",
        "/arenas%2farena-one",
    ):
        with pytest.raises(ValueError):
            project_monitor_read(storage, [metadata], target)


def test_credential_permission_operations_are_not_borrowed_from_constraints():
    from devgraph.auth.arena_activation import ArenaMonitorReadEnrollment
    from devgraph.auth.native_read import RESOURCE

    service = object.__new__(ArenaMonitorReadEnrollment)
    constraints = dict(
        scope="devgraph.work.read",
        operations=["list", "get"],
        work_kinds=["Task"],
        relationship_types=["parent"],
        include_archived=False,
    )
    grants = service._decode_grants(
        [dict(operation="devgraph.work.get", resource=RESOURCE, constraints=constraints)]
    )
    assert grants[0].operations == ("get",)


@pytest.mark.parametrize("reusable", [False, True])
@pytest.mark.parametrize("owner_all,future", [(False, False), (True, False), (True, True)])
def test_real_secs_arena_enrollment_read_replay_and_revocation(
    tmp_path, monkeypatch, reusable, owner_all, future
):
    import hashlib
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from tests.model.test_arena_runtime import add_arena
    from tests.native_read.test_http import sign
    from tests.native_read.test_signed_authority import NOW
    from tests.native_read.test_signed_authority import setup as signed_setup

    from devgraph.api.signed_read import create_signed_read_app
    from devgraph.arenas import ArenaMutations
    from devgraph.auth.arena_access import ArenaMemberPolicy
    from devgraph.auth.arena_activation import ArenaMonitorReadEnrollment
    from devgraph.auth.enforcement import AuditLog
    from devgraph.storage.memory import MemoryGraphStorage

    monkeypatch.setattr("devgraph.auth.native_read._seconds", lambda _: int(NOW.timestamp()))
    monkeypatch.setattr("devgraph.auth.signed_read._seconds", lambda _: int(NOW.timestamp()))
    monkeypatch.setattr("devgraph.auth.holder_grants._seconds", lambda _: int(NOW.timestamp()))
    old, owner, _, policies, _ = signed_setup(tmp_path)
    public = owner.public_key().public_bytes_raw().hex()
    raw = dict(
        schema="devgraph.access-draft.v6",
        issuer=policies[0].issuer,
        receiver=policies[0].receiver,
        write_audience="devgraph://receiver-local",
        lifetime_seconds=900,
        validity=dict(
            schema="castalia.grant-validity.v1",
            mode="until_revoked",
            not_before=int(NOW.timestamp()) - 1,
        ),
        read_grants=[dict(scope="devgraph.arena.read", arena_ids=["arena-one"])],
        write_grants=[],
    )
    root = ArenaAccessDraft.model_validate(raw)
    assignment = ArenaAccessDraft.model_validate(
        dict(raw, subject_public_key=public, installation_id="a" * 32)
    )
    registry = [
        ArenaMemberPolicy.model_validate(
            dict(
                schema="devgraph.member-access-policy.v5",
                revision=1,
                ceiling=root,
                assignments=[assignment],
            )
        )
    ]
    if owner_all:
        from devgraph.auth.arena_access import OwnerArenaAccessDraft, OwnerArenaMemberPolicy

        raw["schema"] = "devgraph.access-draft.v7"
        raw["read_grants"] = [dict(scope="devgraph.arena.read", all_arenas=True)]
        root = OwnerArenaAccessDraft.model_validate(raw)
        registry = [
            OwnerArenaMemberPolicy.model_validate(
                dict(
                    schema="devgraph.member-access-policy.v6",
                    revision=1,
                    ceiling=root,
                    assignments=[assignment],
                )
            )
        ]
    if future:
        from devgraph.auth.arena_access import CollectionArenaAccessDraft

        raw_assignment = json.loads(canonical_json(assignment))
        raw_assignment.update(
            schema="devgraph.access-draft.v8",
            read_grants=[dict(scope="devgraph.arena.read", all_arenas=True)],
        )
        assignment = CollectionArenaAccessDraft.model_validate(raw_assignment)
        raw_registry = json.loads(canonical_json(registry[0]))
        raw_registry["assignments"] = [json.loads(canonical_json(assignment))]
        registry[0] = type(registry[0]).model_validate(raw_registry)
    policy = compile_arena_read(root)
    configured = old.authority.policy()
    old.authority.policy_file.write_text(
        json.dumps(
            SecsReadAuthority.policy_for(
                policy, key_id=configured["key_id"], public_key=configured["public_key"]
            )
        )
    )
    service = ArenaMonitorReadEnrollment(
        path=tmp_path / "arena.sqlite",
        policy=lambda: policy,
        authority=old.authority,
        membership_check=old.membership_check,
        provisioning=SimpleNamespace(registry=lambda: registry[0], check_snapshot=lambda *_: None),
    )
    with old._database() as db:
        membership = json.loads(db.execute("SELECT proof FROM credentials").fetchone()[0])
    storage = MemoryGraphStorage()
    add_arena(ArenaMutations(storage), "arena-one")
    add_arena(ArenaMutations(storage), "private-arena")
    if reusable:
        import os
        from pathlib import Path

        from devgraph.auth.holder_grants import HolderGrantHost, checksum_bytes

        binary = os.environ.get("SECS_HOLDER_GRANT_TEST_BINARY")
        if not binary:
            pytest.skip("actual native holder grant authority required")
        # This fixture supplies assignments directly, without an operator
        # activation journal. Keep its empty journal in a distinct SQLite file,
        # matching production's attached-database transaction boundary.
        import sqlite3
        from contextlib import contextmanager

        journal = tmp_path / "fixture-inbox.sqlite"

        @contextmanager
        def journal_database():
            db = sqlite3.connect(journal)
            try:
                with db:
                    yield db
            finally:
                db.close()

        with journal_database() as db:
            db.execute(
                "CREATE TABLE access_assignments "
                "(owner TEXT, device TEXT, request_id TEXT, active INTEGER)"
            )
            db.execute("CREATE TABLE access_provisioning_head (id INTEGER, revision INTEGER)")
            db.execute("INSERT INTO access_provisioning_head VALUES(1,1)")
        service.provisioning.inbox = SimpleNamespace(path=journal, _database=journal_database)
        HolderGrantHost(
            enrollment=service,
            binary=Path(binary),
            binary_sha256=checksum_bytes(Path(binary)),
            service="service:arena-fixture",
        )
    http = TestClient(
        create_signed_read_app(storage=storage, enrollment=service, audit_log=AuditLog())
    )
    base = "/monitor/wallet-access/v1"
    challenge = http.post(
        base + "/challenges", json=dict(subject_public_key=public, installation_id="a" * 32)
    ).json()
    assert challenge["details"]["schema"] == (
        "devgraph.native-read-scope.v6" if future else "devgraph.native-read-scope.v5"
    )
    if reusable:
        from tests.native_read.test_holder_grants import credential_request

        issued_root = http.post(
            base + "/grants",
            json=dict(challenge=challenge, proof=membership, signature=sign(owner, challenge)),
        )
        assert issued_root.status_code == 200, issued_root.text
        credential = credential_request(owner, issued_root.json()["grant"])["credential"]
        if future:
            assert credential["claims"]["permissions"][0]["constraints"]["all_arenas"] is True
        else:
            assert credential["claims"]["permissions"][0]["constraints"]["arena_ids"] == [
                "arena-one"
            ]
        assert credential["grant"]["claims"]["onward_delegation"] is False
    else:
        result = http.post(
            base + "/enrollments",
            json=dict(challenge=challenge, proof=membership, signature=sign(owner, challenge)),
        )
        assert result.status_code == 200, result.text
        assert result.json()["schema"] == (
            "devgraph.native-read-result.v6" if future else "devgraph.native-read-result.v5"
        )
        credential = result.json()["credential"]
        assert credential["claims"]["expires_at"] - credential["claims"]["issued_at"] == 900
    counter = 0

    def read(path, endpoint=base + "/requests", operation="devgraph.arena.list"):
        nonlocal counter
        counter += 1
        call = dict(
            operation=operation,
            resource="https://work.zenith-research.ca/devgraph",
            parameters=dict(path=path),
        )
        proof = dict(
            schema="castalia.read-presentation.v1",
            credential_digest=hashlib.sha256(canonical_json(credential)).hexdigest(),
            call_digest=hashlib.sha256(canonical_json(call)).hexdigest(),
            receiver=policy.receiver,
            nonce=f"{counter:032x}",
            issued_at=int(NOW.timestamp()),
        )
        domain = b"castalia.read-presentation.v1/signature\0"
        if reusable:
            proof["schema"] = "secs.holder-presentation.v1"
            proof.pop("receiver")
            proof["expires_at"] = int(NOW.timestamp()) + 60
            domain = b"secs.holder-presentation.v1/signature\0"
        proof["signature"] = owner.sign(domain + canonical_json(proof)).hex()
        body = dict(credential=credential, call=call, proof=proof)
        return http.post(endpoint, json=body), body

    response, body = read("/arenas")
    assert response.status_code == 200, response.text
    assert [a["id"] for a in response.json()["items"]] == (
        ["arena-one", "private-arena"] if future else ["arena-one"]
    )
    if future:
        # Keep exactly the issued root and derived credential; creation changes
        # neither assignment, current revision nor signed permission bytes.
        retained = canonical_json(credential)
        add_arena(ArenaMutations(storage), "created-after-issuance")
        assert [a["id"] for a in read("/arenas")[0].json()["items"]] == [
            "arena-one",
            "created-after-issuance",
            "private-arena",
        ]
        assert (
            read("/arenas/created-after-issuance", operation="devgraph.arena.get")[0].status_code
            == 200
        )
        assert canonical_json(credential) == retained
    assert http.post(base + "/requests", json=body).status_code == 401
    assert (
        read(
            "/arenas/arena-one",
            operation="devgraph.arena.get",
            endpoint="/devgraph/native-read/v3/requests",
        )[0].status_code
        == 200
    )
    assert read("/arenas/private-arena", operation="devgraph.arena.get")[0].status_code == (
        200 if future else 404
    )
    assert read("/todos/v2", operation="devgraph.work.list")[0].status_code == 401
    assert read("/arenas/arena-one/members", operation="devgraph.arena.get")[0].status_code == 403
    registry[0] = type(registry[0]).model_validate(
        dict(schema=registry[0].schema_, revision=2, ceiling=root, assignments=[])
    )
    assert read("/arenas")[0].status_code == 401


def test_arena_activation_retains_exact_review_and_old_assignment_compatibility(tmp_path):
    import secrets

    from tests.native_read.test_path_access_flow import request, signed

    from devgraph.auth.arena_access import (
        ArenaGrantSelection,
        ArenaMemberPolicy,
        ArenaOperatorPolicy,
    )
    from devgraph.auth.arena_activation import (
        ArenaAccessOperator,
        ArenaAccessRequestInbox,
        ArenaGrantProvisioning,
    )

    s = setup(tmp_path)
    root_raw = json.loads(canonical_json(standing(s.base[0].ceiling)))
    root_raw["schema"] = "devgraph.access-draft.v6"
    root_raw["read_grants"].append(dict(scope="devgraph.arena.read", arena_ids=["arena-one"]))
    root = ArenaAccessDraft.model_validate(root_raw)
    s.base = [
        ArenaMemberPolicy.model_validate(
            dict(
                schema="devgraph.member-access-policy.v5", revision=3, ceiling=root, assignments=[]
            )
        )
    ]
    raw = s.policies[0].model_dump(mode="json", by_alias=True)
    raw.update(
        schema="devgraph.access-operator-policy.v5",
        revision=4,
        ceiling=json.loads(canonical_json(root)),
    )
    s.policies = [ArenaOperatorPolicy.model_validate(raw)]
    s.inbox = ArenaAccessRequestInbox(
        path=s.inbox.path,
        issuer=root.issuer,
        receiver=root.receiver,
        membership_check=s.inbox.membership_check,
        catalog_policy=lambda: s.policies[0],
        storage=s.storage,
    )
    provisioning = ArenaGrantProvisioning(
        inbox=s.inbox,
        members=lambda: s.base[0],
        operators=lambda: s.policies[0],
        native=lambda: s.native[0],
        native_admission=object(),
    )
    service = ArenaAccessOperator(
        inbox=s.inbox, policy=lambda: s.policies[0], storage=s.storage, provisioning=provisioning
    )
    request(s)

    def invoke(action, **fields):
        value = dict(
            schema="devgraph.access-operator-action.v8",
            action=action,
            action_id=secrets.token_hex(16),
            **fields,
        )
        challenge = service.challenge(s.key, "a" * 32, service.parse_action(value))
        return service.execute(challenge, signed(s.operator, challenge))

    inbox = invoke("inbox")
    assert inbox["activation"]["arena_reads"] is True
    item = inbox["inbox"]["items"][0]
    raw = json.loads(canonical_json(root))
    raw.update(
        subject_public_key=s.public,
        installation_id="a" * 32,
        previous_digest=root.digest,
        write_grants=[],
    )
    selection = ArenaGrantSelection(read=ArenaAccessDraft.model_validate(raw))
    binding = dict(
        request_id=item["request_id"],
        expected_version=item["version"],
        request_digest_sha256=item["request_digest_sha256"],
        previous_assignment_digest=item["previous_assignment_digest"],
    )
    review = invoke("review", selection=selection, **binding)
    assert review["plans"]["activation"]["validity"]["mode"] == "until_revoked"
    result = invoke("approve", selection=selection, review_id=review["review_id"], **binding)
    assert result["activation"]["active"] is True
    active = invoke("inbox")["active_grants"][0]
    assert active["validity"]["mode"] == "until_revoked"
    invoke(
        "revoke",
        request_id=active["request_id"],
        expected_version=active["version"],
        request_digest_sha256=active["request_digest_sha256"],
        previous_assignment_digest=active["assignment_digest"],
        target_public_key=s.public,
        target_installation_id="a" * 32,
        reason="synthetic revoke",
    )
    assert not provisioning.registry().assignments


def test_scoped_topology_carries_only_explicit_arena_records_and_real_edges():
    from tests.model.test_arena_runtime import add_arena, assign
    from tests.model.test_named_work import create, ref

    from devgraph.arenas import ArenaMutations
    from devgraph.auth.monitor_read import MonitorWorkGrant, project_monitor_read
    from devgraph.named_work import NamedWorkMutations
    from devgraph.storage.memory import MemoryGraphStorage

    storage = MemoryGraphStorage()
    arenas, work = ArenaMutations(storage), NamedWorkMutations(storage)
    for name in ("visible-arena", "private-arena"):
        add_arena(arenas, name)
    for name in ("visible", "private"):
        create(work, "Task", name)
        assign(
            arenas,
            "Task",
            name,
            1,
            arena=ref("Arena", "visible-arena", 1 if name == "visible" else 2),
        )
    grant = MonitorWorkGrant(
        scope="devgraph.work.read",
        operations=["list", "get"],
        work_kinds=["Task"],
        work_ids=["Task/visible"],
        relationship_types=["parent"],
        include_archived=False,
    )
    metadata = ArenaReadGrant(scope="devgraph.arena.read", arena_ids=["visible-arena"])
    for target in ("/monitor/topology/v1", "/monitor/snapshot"):
        result = project_monitor_read(storage, [metadata, grant], target)
        text = json.dumps(result)
        assert "visible-arena" in text and "CONTAINS_WORK" in text
        assert "private-arena" not in text and '"private"' not in text
        assert result["access_scope"]["arena_metadata"] is True
        from devgraph.auth.errors import ForbiddenError

        with pytest.raises(ForbiddenError, match="monitor_arena_context_required"):
            project_monitor_read(storage, [grant], target)


@pytest.mark.parametrize("dynamic", [False, True])
def test_narrowed_arena_get_cannot_list_or_disclose_topology_metadata(dynamic):
    from tests.model.test_arena_runtime import add_arena

    from devgraph.arenas import ArenaMutations
    from devgraph.auth.arena_activation import ArenaMonitorReadEnrollment
    from devgraph.auth.errors import ForbiddenError
    from devgraph.auth.monitor_read import project_monitor_read
    from devgraph.auth.native_read import RESOURCE
    from devgraph.storage.memory import MemoryGraphStorage

    storage = MemoryGraphStorage()
    add_arena(ArenaMutations(storage), "arena-one")
    service = object.__new__(ArenaMonitorReadEnrollment)
    grants = service._decode_grants(
        [
            dict(
                operation="devgraph.arena.get",
                resource=RESOURCE,
                constraints=(
                    dict(scope="devgraph.arena.read", all_arenas=True)
                    if dynamic
                    else dict(scope="devgraph.arena.read", arena_ids=["arena-one"])
                ),
            )
        ]
    )
    assert project_monitor_read(storage, grants, "/arenas/arena-one")["id"] == "arena-one"
    with pytest.raises(ForbiddenError):
        project_monitor_read(storage, grants, "/arenas")


def test_holder_arena_path_intersects_metadata_without_work_scope_conversion():
    from devgraph.auth.errors import UnauthenticatedError
    from devgraph.auth.holder_grants import constraint

    permission = dict(
        operation="devgraph.arena.get",
        resource="fixture",
        constraints=dict(scope="devgraph.arena.read", arena_ids=["one", "two"]),
        scope=dict(mode="paths", roots=[[dict(kind="Arena", id="one")]]),
    )
    assert constraint(permission)["constraints"] == dict(
        scope="devgraph.arena.read", arena_ids=["one"]
    )
    for root in ([dict(kind="Arena", id="hidden")], [dict(kind="Project", id="one")]):
        permission["scope"]["roots"] = [root]
        with pytest.raises(UnauthenticatedError):
            constraint(permission)
