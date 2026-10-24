"""Actual secS native contract with disposable keys and retained provider state."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from tests.native_read.test_http import sign
from tests.native_read.test_member_access import members
from tests.native_read.test_signed_authority import NOW

from devgraph.auth.access_policy import MemberAccessPolicy
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.holder_grants import HolderGrantHost, checksum, constraint, permission


def host(tmp_path):
    binary = os.environ.get("SECS_HOLDER_GRANT_TEST_BINARY")
    if not binary:
        pytest.skip("actual native holder grant authority required")
    e, owner, public, membership, registry, root, assignment = members(tmp_path)
    binary = Path(binary)
    h = HolderGrantHost(
        enrollment=e,
        binary=binary,
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        service="service:fixture-graph",
    )
    c = e.challenge(public, "a" * 32, now=NOW)
    args = dict(challenge=c, proof=membership, signature=sign(owner, c), now=NOW)
    return h, e, owner, args, registry, root, assignment


def credential_request(owner, grant, nonce="44" * 16, permissions=None):
    now = int(NOW.timestamp())
    claims = dict(
        schema="secs.holder-credential.v1",
        credential_id="33" * 16,
        grant_digest=checksum(grant),
        holder_public_key=grant["claims"]["holder_public_key"],
        installation_id="a" * 32,
        permissions=permissions or grant["claims"]["permissions"],
        issued_at=now,
        expires_at=now + 60,
    )
    credential = dict(
        grant=grant,
        claims=claims,
        signature=owner.sign(
            b"secs.holder-credential.v1/signature\0" + canonical_json(claims)
        ).hex(),
    )
    call = dict(
        operation="devgraph.work.list",
        resource="https://work.zenith-research.ca/devgraph",
        parameters={"path": "/todos/v2?kind=Task"},
    )
    p = dict(
        schema="secs.holder-presentation.v1",
        credential_digest=checksum(credential),
        call_digest=checksum(call),
        nonce=nonce,
        issued_at=now,
        expires_at=now + 60,
    )
    p["signature"] = owner.sign(
        b"secs.holder-presentation.v1/signature\0" + canonical_json(p)
    ).hex()
    return dict(credential=credential, call=call, proof=p)


def test_issue_exact_recovery_derive_and_revoke(tmp_path):
    h, e, owner, args, _, _, _ = host(tmp_path)
    result = h.issue(**args)
    assert result == h.issue(**args)
    grant = result["grant"]
    assert grant["claims"]["service"] == "service:fixture-graph"
    assert "receiver" not in grant["claims"]
    assert grant["claims"]["onward_delegation"] is False
    request = credential_request(owner, grant)
    assert e.verify_request(**request, now=NOW).read_grants[0].work_ids == (
        "Task/allowed-1",
        "Task/allowed-2",
    )
    with pytest.raises(UnauthenticatedError, match="replay"):
        e.verify_request(**request, now=NOW)
    # Read use does not invoke enrollment or issue any second credential.
    with e._database() as db:
        assert db.execute("SELECT COUNT(*) FROM credentials").fetchone()[0] == 1
        retained = json.loads(db.execute("SELECT proof FROM credentials").fetchone()[0])
        assert retained["grant"] == grant
    e.revoke("dgrg_" + grant["claims"]["grant_id"])
    with pytest.raises(UnauthenticatedError):
        e.verify_request(**credential_request(owner, grant, "55" * 16), now=NOW)


def test_narrowing_and_root_substitution_deny_existing_credentials(tmp_path):
    h, e, owner, args, registry, root, _ = host(tmp_path)
    grant = h.issue(**args)["grant"]
    bad = json.loads(json.dumps(grant))
    bad["claims"]["service"] = "service:other"
    with pytest.raises(UnauthenticatedError):
        e.verify_request(**credential_request(owner, bad), now=NOW)
    registry[0] = MemberAccessPolicy.model_validate(
        dict(
            schema="devgraph.member-access-policy.v1",
            revision=registry[0].revision + 1,
            ceiling=root,
            assignments=[],
        )
    )
    with pytest.raises(UnauthenticatedError):
        e.verify_request(**credential_request(owner, grant), now=NOW)


def test_scope_factorization_roundtrip_preserves_constraint(tmp_path):
    original = dict(
        operation="devgraph.work.get",
        resource="work",
        constraints={
            "schema": "devgraph.path-read-grant.v1",
            "operations": ["get"],
            "resource_paths": [
                {"schema": "devgraph.resource-path.v1", "path": ["Arena/a", "Project/p"]}
            ],
        },
    )
    assert constraint(permission(original)) == original


def alias(tmp_path, provider, registry):
    from devgraph.auth.member_read import MemberReadEnrollment
    from devgraph.auth.signed_read import SecsReadAuthority

    tmp_path.mkdir(mode=0o700)
    receiver = "https://tailnet.example:9443"
    policy = provider.policy().model_copy(update={"receiver": receiver})
    raw = json.loads(canonical_json(registry[0]))
    raw["ceiling"]["receiver"] = receiver
    for g in raw["assignments"]:
        g["receiver"] = receiver
    selected = MemberAccessPolicy.model_validate(raw)
    authority = provider.authority.policy()
    authority["receiver"] = receiver
    policy_file = tmp_path / "authority.json"
    policy_file.write_text(json.dumps(authority))
    policy_file.chmod(0o600)
    enrollment = MemberReadEnrollment(
        path=tmp_path / "alias.sqlite",
        policy=lambda: policy,
        membership_check=provider.membership_check,
        authority=SecsReadAuthority(
            binary=provider.authority.binary,
            policy_file=policy_file,
            issuer_key_file=provider.authority.issuer_key_file,
        ),
        members=lambda: selected,
    )
    root = HolderGrantHost(
        enrollment=enrollment,
        provider_enrollment=provider,
        binary=Path(os.environ["SECS_HOLDER_GRANT_TEST_BINARY"]),
        binary_sha256=hashlib.sha256(
            Path(os.environ["SECS_HOLDER_GRANT_TEST_BINARY"]).read_bytes()
        ).hexdigest(),
        service="service:fixture-graph",
    )
    return root, enrollment


def test_canonical_grant_crosses_access_points_but_replay_and_revocation_do_not(tmp_path):
    h, e, owner, args, registry, _, _ = host(tmp_path)
    remote, a = alias(tmp_path / "alias", e, registry)
    grant = h.issue(**args)["grant"]
    request = credential_request(owner, grant)
    assert a.verify_request(**request, now=NOW).read_grants
    with pytest.raises(UnauthenticatedError, match="replay"):
        e.verify_request(**request, now=NOW)
    # Restarting the adapter must retain the same status and replay ledger.
    restarted = HolderGrantHost(
        enrollment=a,
        provider_enrollment=e,
        binary=remote.binary,
        binary_sha256=remote.binary_sha256,
        service=remote.service,
    )
    with pytest.raises(UnauthenticatedError, match="replay"):
        restarted.verify_request(**request, now=NOW)
    e.revoke("dgrg_" + grant["claims"]["grant_id"])
    with pytest.raises(UnauthenticatedError, match="revoked"):
        a.verify_request(**credential_request(owner, grant, "66" * 16), now=NOW)


def test_alias_issuance_retains_root_in_canonical_transaction(tmp_path):
    _, e, owner, args, registry, _, _ = host(tmp_path)
    remote, a = alias(tmp_path / "alias", e, registry)
    c = a.challenge(args["challenge"]["subject_public_key"], "a" * 32, now=NOW)
    grant = remote.issue(c, args["proof"], sign(owner, c), now=NOW)["grant"]
    assert e.verify_request(**credential_request(owner, grant), now=NOW).read_grants
    with e._database() as db:
        assert db.execute("SELECT COUNT(*) FROM holder_roots").fetchone()[0] == 1
    with a._database() as db:
        assert db.execute(
            "SELECT used FROM challenges WHERE id=?", (c["request_id"],)
        ).fetchone() == (1,)
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='holder_roots'").fetchone()
            is None
        )


def test_current_issuer_rotation_invalidates_existing_root_without_restart(tmp_path):
    h, e, owner, args, _, _, _ = host(tmp_path)
    grant = h.issue(**args)["grant"]
    policy = e.authority.policy()
    policy["key_id"] = "rotated-key"
    e.authority.policy_file.write_text(json.dumps(policy))
    with pytest.raises(UnauthenticatedError, match="holder_issuer_changed"):
        e.verify_request(**credential_request(owner, grant), now=NOW)


def test_concurrent_original_retry_commits_only_one_root(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    h, e, _, args, _, _, _ = host(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: h.issue(**args), range(2)))
    assert results[0] == results[1]
    with e._database() as db:
        assert db.execute("SELECT COUNT(*) FROM holder_roots").fetchone() == (1,)


def test_root_and_challenge_roll_back_together(tmp_path, monkeypatch):
    h, e, _, args, _, _, _ = host(tmp_path)
    retain = h.retain

    def interrupted(*values):
        retain(*values)
        raise UnauthenticatedError("retention_interrupted")

    monkeypatch.setattr(h, "retain", interrupted)
    with pytest.raises(UnauthenticatedError, match="retention_interrupted"):
        h.issue(**args)
    with e._database() as db:
        assert db.execute("SELECT COUNT(*) FROM holder_roots").fetchone() == (0,)
        assert db.execute(
            "SELECT used FROM challenges WHERE id=?", (args["challenge"]["request_id"],)
        ).fetchone() == (0,)
    monkeypatch.setattr(h, "retain", retain)
    assert h.issue(**args)["grant"]


def test_runtime_selects_one_explicit_canonical_provider_and_rejects_chains(tmp_path, monkeypatch):
    import devgraph.auth.holder_grants as adapter
    import devgraph.signed_read_runtime as runtime

    class Selected(Exception):
        pass

    fields = (
        "member_policy_file",
        "enrollment_policy_file",
        "authority_policy_file",
        "membership_trust_file",
        "revoked_memberships_file",
        "enrollment_database_file",
        "secs_binary",
        "issuer_key_file",
        "holder_grant_binary",
    )
    base = dict(
        schema="devgraph.signed-read-runtime.v2",
        **{k: str(tmp_path / k) for k in fields},
        secs_binary_sha256="a" * 64,
        holder_grant_binary_sha256="b" * 64,
        canonical_service_id="service:fixture",
    )
    canonical, endpoint = tmp_path / "canonical.json", tmp_path / "endpoint.json"
    provider = dict(base, holder_grant_provider_runtime_file=str(canonical))
    selected = dict(
        base,
        holder_grant_provider_runtime_file=str(canonical),
        enrollment_database_file=str(tmp_path / "endpoint.sqlite"),
    )

    def save(path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    save(canonical, provider)
    save(endpoint, selected)
    monkeypatch.setenv("DEVGRAPH_SIGNED_READ_CONFIG", str(endpoint))
    monkeypatch.setenv("DEVGRAPH_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(runtime, "RetainedAuditLog", lambda _: object())
    loaded = []

    def load(config, **_):
        loaded.append(config.enrollment_database_file)
        return config.enrollment_database_file

    monkeypatch.setattr(runtime, "load_enrollment", load)

    def host(**options):
        assert options["enrollment"] == Path(selected["enrollment_database_file"])
        assert options["provider_enrollment"] == Path(provider["enrollment_database_file"])
        raise Selected()

    monkeypatch.setattr(adapter, "HolderGrantHost", host)
    with pytest.raises(Selected):
        runtime.create_app()
    assert loaded == [
        Path(selected["enrollment_database_file"]),
        Path(provider["enrollment_database_file"]),
    ]
    save(canonical, dict(provider, holder_grant_provider_runtime_file=str(tmp_path / "other.json")))
    with pytest.raises(ValueError, match="without chaining"):
        runtime.create_app()
    save(canonical, dict(provider, issuer_key_file=str(tmp_path / "different-key")))
    with pytest.raises(ValueError, match="ownership or eligibility"):
        runtime.create_app()


def test_opt_in_holder_transport_budget_preserves_legacy_body_limit():
    import asyncio

    from starlette.requests import Request

    from devgraph.api.native_read import _body
    from devgraph.lan_access_runtime import LanAccessBoundary
    from devgraph.public_read_runtime import PublicReadBoundary

    raw = json.dumps(dict(credential={"padding": "x" * 70000}, call={}, proof={})).encode()

    def request(data):
        async def receive():
            return {"type": "http.request", "body": data, "more_body": False}

        return Request({"type": "http", "method": "POST", "path": "/"}, receive)

    with pytest.raises(UnauthenticatedError):
        asyncio.run(_body(request(raw), {"credential", "call", "proof"}))
    assert (
        asyncio.run(_body(request(raw), {"credential", "call", "proof"}, maximum_bytes=131072))[
            "call"
        ]
        == {}
    )
    with pytest.raises(UnauthenticatedError):
        asyncio.run(_body(request(raw * 2), {"credential", "call", "proof"}, maximum_bytes=131072))
    for boundary in [PublicReadBoundary, LanAccessBoundary]:
        instance = object.__new__(boundary)
        assert instance.body_limit("/monitor/wallet-access/v1/requests", "POST") == 131072
        assert instance.body_limit("/monitor/wallet-access/v1/challenges", "POST") == 65536
