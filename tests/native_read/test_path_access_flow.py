"""Preset -> signed request -> exact decision -> durable scoped authority.

All identities, issuer keys, graphs and policies are synthetic fixtures. No user
Wallet, native provisioning, trust installation or live services are accessed.
"""

import hashlib
import secrets
import sqlite3
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.auth.test_resource_paths import ids, move, path
from tests.auth.test_write_scope import hierarchy
from tests.native_read.test_access_operator import signed
from tests.native_read.test_write_provisioning import setup as old_setup

from devgraph.api.access_requests import register_access_request_routes
from devgraph.auth.access_catalog import CatalogOperatorPolicy, catalog
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.path_access import (
    PathMemberPolicy,
    Requests,
)
from devgraph.auth.path_activation import (
    PathAccessOperator,
    PathAccessRequestInbox,
    PathGrantProvisioning,
)
from devgraph.storage.memory import MemoryGraphStorage


def setup(tmp_path):
    s = old_setup(tmp_path)
    raw = s.policies[0].ceiling.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    raw["schema"] = "devgraph.access-draft.v4"
    s.base = [
        PathMemberPolicy.model_validate(
            dict(schema="devgraph.member-access-policy.v3", revision=2, ceiling=raw, assignments=[])
        )
    ]
    s.policies = [
        CatalogOperatorPolicy.model_validate(
            dict(
                schema="devgraph.access-operator-policy.v3",
                revision=3,
                operator_public_keys=[s.key],
                ceiling=raw,
                published_paths=[dict(resource_path=path("Arena/a"), discover_descendants=True)],
            )
        )
    ]
    s.storage = MemoryGraphStorage()
    hierarchy(s.storage)
    s.inbox = PathAccessRequestInbox(
        path=s.inbox.path,
        issuer=s.inbox.issuer,
        receiver=s.inbox.receiver,
        membership_check=s.inbox.membership_check,
        catalog_policy=lambda: s.policies[0],
        storage=s.storage,
    )
    restart(s)
    return s


def restart(s):
    s.provisioning = PathGrantProvisioning(
        inbox=s.inbox,
        members=lambda: s.base[0],
        operators=lambda: s.policies[0],
        native=lambda: s.native[0],
    )
    s.service = PathAccessOperator(
        inbox=s.inbox, policy=lambda: s.policies[0], storage=s.storage, provisioning=s.provisioning
    )


def request(s, preference=None):
    available = s.inbox.public_catalog()
    intent = dict(
        schema="devgraph.access-intent.v2",
        preference=preference
        or dict(
            id="contributor",
            version=1,
            catalog_digest_sha256=available["catalog_digest_sha256"],
            resource_paths=[path("Arena/a", "Initiative/root-a", "Project/project-a")],
        ),
    )
    act = Requests.validate_python(
        dict(
            schema="devgraph.access-request-action.v2",
            action="submit",
            request_id=secrets.token_hex(16),
            subject_public_key=s.public,
            installation_id="a" * 32,
            intent=intent,
        )
    )
    challenge = s.inbox.wallet_challenge(act)
    result = s.inbox.execute_wallet(challenge, s.proof, signed(s.holder, challenge))
    return act, result


def invoke(s, kind, schema="devgraph.access-operator-action.v5", **fields):
    value = dict(schema=schema, action=kind, action_id=secrets.token_hex(16), **fields)
    act = s.service.parse_action(value)
    challenge = s.service.challenge(s.key, "a" * 32, act)
    return s.service.execute(challenge, signed(s.operator, challenge))


def selection(s, item, *, mixed=True):
    root = s.policies[0].ceiling
    base = root.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    base.update(subject_public_key=s.public, previous_digest=root.digest)
    ref = dict(
        id="contributor",
        version=1,
        catalog_digest_sha256=catalog(s.policies[0])["catalog_digest_sha256"],
    )
    selected = path("Arena/a", "Initiative/root-a", "Project/project-a")
    read = dict(base, installation_id="a" * 32, preset=ref)
    read.pop("write_grants", None)
    read["read_grants"][0].update(
        schema="devgraph.path-read-grant.v1", resource_paths=[selected], work_kinds=["Task"]
    )
    write = dict(base, preset=ref)
    write.pop("read_grants", None)
    write["write_grants"][0].update(
        schema="devgraph.path-write-grant.v1", resource_paths=[selected]
    )
    return dict(
        request_id=item["request_id"],
        request_digest_sha256=item["request_digest_sha256"],
        expected_version=1,
        selection=dict(read=read, **({"write": write} if mixed else {})),
        previous_assignment_digest=item["previous_assignment_digest"],
        **(
            {"previous_write_assignment_digest": item["previous_write_assignment_digest"]}
            if mixed
            else {}
        ),
    )


def approve(s, *, mixed=True):
    action, result = request(s)
    inbox = invoke(s, "inbox", schema="devgraph.access-operator-action.v4")
    item = next(i for i in inbox["inbox"]["items"] if i["request_id"] == action.request_id)
    params = selection(s, item, mixed=mixed)
    schema = "devgraph.access-operator-action.v5" if mixed else "devgraph.access-operator-action.v4"
    reviewed = invoke(s, "review", schema=schema, **params)
    return (
        invoke(s, "approve", schema=schema, review_id=reviewed["review_id"], **params),
        params,
        action,
    )


def test_public_catalog_discloses_only_published_roots_and_presets_are_not_authority(tmp_path):
    s = setup(tmp_path)
    available = s.inbox.public_catalog()
    assert {p["id"] for p in available["presets"]} == {"contributor", "project-manager"}
    assert {p["resource"] for p in available["scopes"]} == {
        "Arena/a",
        "Initiative/root-a",
        "Project/project-a",
    }
    assert not s.provisioning.registry().assignments
    s.policies[0] = s.policies[0].model_copy(update={"published_paths": ()})
    assert s.inbox.public_catalog()["scopes"] == []
    assert "operator_public_keys" not in available and "ceiling" not in available


def test_request_exact_preferences_survive_restart_without_granting(tmp_path):
    s = setup(tmp_path)
    act, result = request(s)
    assert result["status"] == "pending"
    restart(s)
    status = act.model_copy(update={"action": "status", "intent": None})
    ch = s.inbox.wallet_challenge(status)
    assert s.inbox.execute_wallet(ch, s.proof, signed(s.holder, ch))["status"] == "pending"
    item = next(i for i in s.inbox.pending()["items"] if i["request_id"] == act.request_id)
    assert item["intent"]["preference"]["id"] == "contributor"
    assert s.provisioning.registry().assignments == ()


def test_atomic_mixed_activation_persistence_reuse_and_revoke(tmp_path):
    s = setup(tmp_path)
    result, params, act = approve(s)
    assert result["activation"]["access_mode"] == "read_write" and result["status"] == "active"
    restart(s)
    read = s.provisioning.registry().assignment(s.public, "a" * 32, int(time.time()))
    with s.inbox._database() as db:
        write = s.provisioning.write_current(db, s.public)
        assert write and read.read_grants[0].resource_paths == write.write_grants[0].resource_paths
        assert db.execute(
            "SELECT count(*) FROM access_provisioning_audit WHERE action_id LIKE ?",
            (result["action_id"] + "%",),
        ).fetchone() == (2,)
    assert ids(s.storage, read.read_grants[0]) == ({"Task/child"}, 1)
    move(s.storage)
    assert ids(s.storage, read.read_grants[0]) == (set(), 0)
    listing = invoke(s, "inbox", schema="devgraph.access-operator-action.v4")
    active = next(g for g in listing["active_grants"] if g["request_id"] == act.request_id)
    assert active["access_mode"] == "read_write"
    revoked = invoke(
        s,
        "revoke",
        request_id=act.request_id,
        request_digest_sha256=params["request_digest_sha256"],
        expected_version=2,
        target_public_key=s.public,
        target_installation_id="a" * 32,
        reason="Fixture finished",
        previous_assignment_digest=read.digest,
        previous_write_assignment_digest=write.digest,
    )
    assert not revoked["activation"]["active"]
    restart(s)
    assert s.provisioning.registry().assignments == ()
    with s.inbox._database() as db:
        assert s.provisioning.write_current(db, s.public) is None


def test_failed_write_admission_rolls_back_both_halves(tmp_path):
    s = setup(tmp_path)
    s.native[0] = s.native[0].model_copy(
        update={
            "rules": (s.native[0].rules[0].model_copy(update={"effect": "deny"}),),
            "policy_version": 2,
        }
    )
    with pytest.raises(UnauthenticatedError, match="native_admission"):
        approve(s)
    assert not s.provisioning.registry().assignments
    with s.inbox._database() as db:
        assert db.execute("SELECT count(*) FROM write_assignments").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM access_decisions").fetchone() == (0,)


def test_stale_template_and_unpublished_path_are_rejected(tmp_path):
    s = setup(tmp_path)
    for ref in [
        dict(id="contributor", version=1, catalog_digest_sha256="0" * 64),
        dict(
            id="contributor",
            version=1,
            catalog_digest_sha256=catalog(s.policies[0])["catalog_digest_sha256"],
            resource_paths=[path("Arena/b")],
        ),
    ]:
        with pytest.raises((ValueError, UnauthenticatedError)):
            request(s, ref)


def test_catalog_route_is_https_only_and_no_grant_material(tmp_path):
    s = setup(tmp_path)
    app = FastAPI()
    register_access_request_routes(app, s.inbox)
    client = TestClient(app, base_url="https://example.test")
    response = client.get("/devgraph/access-requests/v2/catalog")
    assert response.status_code == 200 and response.json()["receiver"] == "https://example.test"
    assert client.get("/devgraph/access-requests/v2/catalog?key=bad").status_code == 400
    assert (
        TestClient(app, base_url="http://example.test")
        .get("/devgraph/access-requests/v2/catalog")
        .status_code
        == 400
    )


def test_failure_during_second_assignment_leaves_neither_grant(tmp_path):
    s = setup(tmp_path)
    with s.inbox._database() as db:
        db.execute(
            "CREATE TRIGGER fixture_reject_write BEFORE INSERT ON write_assignments "
            "BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END"
        )
    with pytest.raises(sqlite3.Error):
        approve(s)
    with s.inbox._database() as db:
        assert db.execute("SELECT count(*) FROM access_assignments").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM write_assignments").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM access_decisions").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM access_provisioning_audit").fetchone() == (0,)


def test_real_secs_enrollment_reuses_one_credential_across_live_path_changes(tmp_path):
    import os
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from tests.native_read.test_http import sign

    from devgraph.auth.monitor_read import project_monitor_read
    from devgraph.auth.path_access import compile_path_read
    from devgraph.auth.path_activation import PathMonitorReadEnrollment
    from devgraph.auth.signed_read import SecsReadAuthority

    binary = os.environ.get("SECS_READ_AUTHORITY_TEST_BINARY")
    if not binary:
        pytest.skip("matched secS fixture binary required")
    s = setup(tmp_path)
    approve(s)
    issuer = Ed25519PrivateKey.generate()
    key = tmp_path / "path-issuer.key"
    key.write_text(issuer.private_bytes_raw().hex())
    key.chmod(0o600)
    root = compile_path_read(s.base[0].ceiling)
    policy = tmp_path / "path-read-authority.json"
    policy.write_bytes(
        canonical_json(
            SecsReadAuthority.policy_for(
                root, key_id="path-fixture", public_key=issuer.public_key().public_bytes_raw().hex()
            )
        )
    )
    policy.chmod(0o600)
    service = PathMonitorReadEnrollment(
        path=tmp_path / "path-enrollment.sqlite",
        provisioning=s.provisioning,
        policy=lambda: root,
        membership_check=s.inbox.membership_check,
        authority=SecsReadAuthority(binary=Path(binary), policy_file=policy, issuer_key_file=key),
    )
    ch = service.challenge(s.public, "a" * 32)
    assert ch["details"]["schema"] == "devgraph.native-read-scope.v4"
    result = service.enroll(ch, s.proof, sign(s.holder, ch))
    assert result["schema"] == "devgraph.native-read-result.v4"
    credential = result["credential"]

    def read():
        call = dict(
            operation="devgraph.work.list",
            resource="https://work.zenith-research.ca/devgraph",
            parameters={"path": "/todos/v2?limit=100&archived=exclude"},
        )
        proof = dict(
            schema="castalia.read-presentation.v1",
            credential_digest=checksum(credential),
            call_digest=checksum(call),
            receiver="https://example.test",
            nonce=secrets.token_hex(16),
            issued_at=int(time.time()),
        )
        proof["signature"] = s.holder.sign(
            b"castalia.read-presentation.v1/signature\0" + canonical_json(proof)
        ).hex()
        context = service.verify_request(credential=credential, call=call, proof=proof)
        return project_monitor_read(s.storage, context.read_grants, call["parameters"]["path"])[
            "total"
        ]

    assert read() == 1
    move(s.storage)
    assert read() == 0
    move(s.storage, source="project-b", target="project-a")
    assert read() == 1
    assert result["credential"] == credential
    # A new, owner-approved assignment may widen the enrollment ceiling. It
    # invalidates old credentials instead of silently enlarging them on renewal.
    action, _ = request(s)
    listing = invoke(s, "inbox", schema="devgraph.access-operator-action.v4")
    item = next(i for i in listing["inbox"]["items"] if i["request_id"] == action.request_id)
    params = selection(s, item, mixed=False)
    params["selection"]["read"]["read_grants"][0]["work_kinds"] = ["Project", "Task"]
    reviewed = invoke(s, "review", schema="devgraph.access-operator-action.v4", **params)
    invoke(
        s,
        "approve",
        schema="devgraph.access-operator-action.v4",
        review_id=reviewed["review_id"],
        **params,
    )
    with pytest.raises(UnauthenticatedError):
        read()
    ch = service.challenge(s.public, "a" * 32)
    credential = service.enroll(ch, s.proof, sign(s.holder, ch))["credential"]
    assert read() == 2


def checksum(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def test_mixed_decision_cannot_be_half_revoked(tmp_path):
    s = setup(tmp_path)
    _, params, act = approve(s)
    read = s.provisioning.registry().assignment(s.public, "a" * 32, int(time.time()))
    with pytest.raises(UnauthenticatedError, match="both_assignments_required"):
        invoke(
            s,
            "revoke",
            schema="devgraph.access-operator-action.v4",
            request_id=act.request_id,
            request_digest_sha256=params["request_digest_sha256"],
            expected_version=2,
            target_public_key=s.public,
            target_installation_id="a" * 32,
            reason="Incomplete revoke",
            previous_assignment_digest=read.digest,
        )
    assert s.provisioning.registry().assignment(s.public, "a" * 32, int(time.time()))
    with s.inbox._database() as db:
        assert s.provisioning.write_current(db, s.public)


def test_public_project_publication_does_not_reveal_ancestors(tmp_path):
    from devgraph.auth.access_catalog import PublishedPath

    s = setup(tmp_path)
    s.policies[0] = s.policies[0].model_copy(
        update={
            "published_paths": (
                PublishedPath(resource_path=path("Project/project-a"), discover_descendants=True),
            )
        }
    )
    rows = s.inbox.public_catalog()["scopes"]
    assert len(rows) == 1
    assert rows[0]["resource_path"] == path("Project/project-a").model_dump(
        mode="json", by_alias=True
    )
    assert rows[0]["parent"] is None


def test_path_intersection_preserves_all_ancestor_conditions():
    from devgraph.resource_paths import ResourcePath, intersect_paths

    a = ResourcePath.model_validate(path("Arena/a", "Project/p"))
    b = ResourcePath.model_validate(path("Initiative/i", "Project/p"))
    assert [x.path for x in intersect_paths((a,), (b,))] == [
        ("Arena/a", "Initiative/i", "Project/p")
    ]
    c = ResourcePath.model_validate(path("Arena/b"))
    assert intersect_paths((a,), (c,)) == ()


def test_runtime_v7_loads_explicit_path_models_and_keeps_native_pin(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from devgraph.auth.path_access import compile_path_read
    from devgraph.auth.path_activation import PathMonitorReadEnrollment
    from devgraph.auth.signed_read import SecsReadAuthority
    from devgraph.ops.named_work_v2_receiver import LocalNamedWorkV2Receiver
    from devgraph.signed_read_runtime import ReadRuntimeConfiguration, load_enrollment

    (tmp_path / "fixture").mkdir(mode=0o700)
    s = setup(tmp_path / "fixture")
    files = {}
    values = {
        "enrollment_policy_file": compile_path_read(s.base[0].ceiling),
        "member_policy_file": s.base[0],
        "access_operator_policy_file": s.policies[0],
        "write_native_policy_file": s.native[0],
        "membership_trust_file": {},
        "revoked_memberships_file": {
            "schema": "devgraph.revoked-memberships.v1",
            "membership_ids": [],
        },
        "authority_policy_file": SecsReadAuthority.policy_for(
            compile_path_read(s.base[0].ceiling), key_id="fixture", public_key=s.key
        ),
    }
    for name, value in values.items():
        target = tmp_path / (name + ".json")
        target.write_bytes(canonical_json(value))
        target.chmod(0o600)
        files[name] = target
    binary = tmp_path / "fixture-never-executed"
    binary.write_bytes(b"synthetic composition only")
    files.update(
        secs_binary=binary,
        issuer_key_file=tmp_path / "unopened-key",
        enrollment_database_file=tmp_path / "enroll.sqlite",
        access_inbox_database_file=tmp_path / "runtime-inbox.sqlite",
    )
    seen = []
    registry = SimpleNamespace(require_binding=lambda *binding: seen.append(binding))
    monkeypatch.setattr(
        LocalNamedWorkV2Receiver,
        "_load_verifier",
        lambda _: SimpleNamespace(config=SimpleNamespace(policy_registry=registry)),
    )
    config = ReadRuntimeConfiguration.model_validate(
        dict(
            schema="devgraph.signed-read-runtime.v7",
            **files,
            secs_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        )
    )
    enrollment = load_enrollment(config, data_root=tmp_path / "receiver")
    assert isinstance(enrollment, PathMonitorReadEnrollment)
    assert isinstance(enrollment.provisioning, PathGrantProvisioning)
    assert seen and all(
        binding == (s.native[0].policy_id, s.native[0].policy_version, s.native[0].digest)
        for binding in seen
    )
    legacy = config.model_copy(update={"schema_": "devgraph.signed-read-runtime.v6"})
    with pytest.raises(ValueError):
        load_enrollment(legacy, data_root=tmp_path / "receiver")
