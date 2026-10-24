import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509

from devgraph.ops import owner_install as m

OWNER = "11" * 32
HOLDER = "22" * 32


def store(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(m.encoded(value) if isinstance(value, dict) else value)
    path.chmod(mode)
    return path


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home = tmp_path.resolve()
    base = home / "Library/Application Support/Zenith/Devgraph"
    data = base / "data"
    native = home / "Library/Application Support/Zenith/secS"
    config = SimpleNamespace(data_root=data, python_executable=Path("/usr/bin/python3"))
    config_file = store(base / "local.json", {"instance": "fixture"})
    resource = "5b4e45ac-6e51-43ee-aad0-f1818a43f252"
    registration = store(
        base / "resource-registration.json",
        dict(
            schema="zenith.local-resource.v1",
            resource_id=resource,
            kind="devgraph",
            name="Real fixture graph",
            endpoint="http://127.0.0.1:8080",
            connection=None,
            updated_at=1,
        ),
    )
    rule = dict(
        actor_id="pubkey:sha256:" + "aa" * 32,
        effect="allow",
        status="active",
        operation="devgraph.work.patch.v1",
        resource="Task/old",
        resource_match="exact",
        not_before=1,
        not_after=2000000000,
    )
    old = dict(
        schema="secs-devgraph-work-policy.v1",
        schema_version=1,
        audience="devgraph://receiver-local",
        policy_id="real-work",
        policy_version=31,
        rules=[
            rule,
            dict(rule, effect="deny", resource="Task/denied", status="revoked"),
        ],
    )
    parsed = m.NativeWritePolicy.model_validate(old)
    bundle = native / "authority/devgraph.work.v1"
    store(bundle / "receiver-policy.json", old)
    registry = dict(
        schema="secs-public-key-registry.v1",
        keys=[
            dict(
                key_id="same-key",
                public_key_base64url=base64.urlsafe_b64encode(b"3" * 32).decode().rstrip("="),
                not_before=1,
                not_after=2000000000,
                status="active",
            )
        ],
    )
    store(bundle / "secs-public-key-registry.json", registry)
    store(bundle / "producer-manifest.json", {"preserved": "manifest"})
    receiver = dict(
        schema="devgraph-secs-work-receiver.v1",
        schema_version=1,
        audience="devgraph://receiver-local",
        stable_issuer="secs-real-work",
        policy_binding=dict(
            policy_id="real-work", policy_version=31, policy_digest_sha256=parsed.digest
        ),
    )
    store(data / "secrets/secs-magik/devgraph.work.v1/receiver.json", receiver)
    store(
        data / "secrets/secs-magik/devgraph.work.v1/secs-public-key-registry.json",
        registry,
    )
    store(native / "bin/secs-devgraph-work-v1", b"old-native", 0o700)
    artifacts = {}
    for name in (
        "secs-devgraph-work-v1",
        "secs-devgraph-work-v2",
        "secs-read-authority-v1",
    ):
        p = store(base / "candidates" / name, ("fixture-" + name).encode(), 0o700)
        artifacts[name] = dict(path=str(p), sha256=m.sha(p.read_bytes()))
    store(
        base / "owner-runtime/artifacts.json",
        dict(schema="devgraph.owner-runtime-artifacts.v1", binaries=artifacts),
    )
    plan = dict(
        schema="zenith.devgraph-owner-setup.v2",
        status="prepared_not_applied",
        resource_id=resource,
        resource_name="Real fixture graph",
        local_endpoint="http://127.0.0.1:8080",
        registration_sha256=m.sha(registration.read_bytes()),
        configuration_sha256=m.sha(config_file.read_bytes()),
        owner_public_key=OWNER,
        wallet_endpoint="https://192.168.10.20:18443",
        preset="custom",
        write_operations=["patch"],
        read_operations=["list", "get", "relationships"],
        scope_mode="global",
        paths=[],
        not_before=100,
        not_after=None,
        publish_hierarchy=False,
    )
    plan_file = store(base / "chosen-plan.json", plan)
    store(home / ".local/bin/devgraph", b"old-cli", 0o755)
    events = []
    status = dict(
        ready=True,
        policy_id="real-work",
        policy_version=31,
        policy_digest_sha256=parsed.digest,
        secs_verifier_key_id="same-key",
        registry=registry,
    )

    def invoke(args, **kwargs):
        assert str(home) in str(args[0]), "Fixture must not invoke a real native binary"
        events.append([str(x) for x in args])
        if args[2] == "status":
            return status
        if args[2] in ("snapshot", "verify-snapshot"):
            return dict(
                file_hashes={"fixture": "verified"},
                policy_digest_sha256=parsed.digest,
                secs_verifier_key_id="same-key",
            )
        assert args[2] == "provision"
        policy = m.StandingNativePolicy.model_validate_json(Path(args[4]).read_bytes())
        store(
            bundle / "receiver-policy.json",
            policy.model_dump(mode="json", by_alias=True),
        )
        status.update(policy_version=policy.policy_version, policy_digest_sha256=policy.digest)
        return status

    monkeypatch.setattr(m, "load_local_config", lambda _: config)
    monkeypatch.setattr(
        m,
        "cli_read",
        lambda _, args: (
            {"healthy": True}
            if args[:2] == ["local", "status"]
            else (
                {
                    "ready": True,
                    "signer_matches": True,
                    "receiver_matches": True,
                    "actor_id": "existing-agent",
                    "operations_authorized": 15,
                    "arena_operations_authorized": 4,
                }
                if args[:3] == ["auth", "work", "status"]
                else {"items": []}
            )
        ),
    )
    monkeypatch.setattr(m, "invoke", invoke)
    monkeypatch.setattr(
        m, "copy_package", lambda _, dest: store(dest / "__init__.py", b"# fixture")
    )
    return SimpleNamespace(
        home=home,
        base=base,
        data=data,
        native=native,
        plan=plan,
        plan_file=plan_file,
        old=old,
        registry=registry,
        events=events,
        store=store,
        config=config,
    )


def prepare(s):
    return m.prepare(s.plan_file, home=s.home, now=1000)


def test_preparation_stages_real_scopes_without_grants_authority_or_listener(setup):
    s = setup
    before = (s.native / "authority/devgraph.work.v1/receiver-policy.json").read_bytes()
    out = prepare(s)
    r = out["review"]
    root = Path(r["root"])
    assert r["status"] == "prepared_not_activated" and r["members_preassigned"] == 0
    assert (
        r["native_key_unchanged"]
        and r["existing_rules_preserved"] == 2
        and r["new_native_rules"] == 0
        and r["native_admission_on_grant"] is True
    )
    assert r["native_policy_version"] == 32 and r["owner_public_key"] == OWNER
    assert s.events and all(row[2] == "status" for row in s.events)
    assert (s.native / "authority/devgraph.work.v1/receiver-policy.json").read_bytes() == before
    assert not (s.native / "bin/secs-devgraph-work-v2").exists()
    assert not (s.home / "Library/LaunchAgents").exists()
    assert json.loads((root / "authority/members.json").read_bytes()).get("assignments", []) == []
    cert = x509.load_pem_x509_certificate((root / "tls/tls.crt").read_bytes())
    assert not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert r["connection"]["receiver"] == s.plan["wallet_endpoint"]
    assert "preset-qualification" not in json.dumps(r)
    assert (root / "authority/read.key").stat().st_mode & 0o777 == 0o600
    assert m.checked_review(s.plan_file, out["review_digest"], home=s.home)[2] == r


def test_conversion_preserves_finite_deny_and_revoked_history(setup):
    s = setup
    p = m.converted_native(
        s.old,
        [HOLDER],
        ["devgraph.work.patch.v2"],
        dict(schema="castalia.grant-validity.v1", mode="until_revoked", not_before=1000),
    )
    raw = p.model_dump(mode="json", by_alias=True)
    for before, after in zip(s.old["rules"], raw["rules"][:2], strict=True):
        expected = dict(before)
        start = expected.pop("not_before")
        end = expected.pop("not_after")
        expected["validity"] = dict(
            schema="castalia.grant-validity.v1",
            mode="dated",
            not_before=start,
            not_after=end,
        )
        assert after == expected
    assert len(p.rules) == 6 and p.policy_version == 32
    assert all(r.actor_id != "pubkey:sha256:" + "aa" * 32 for r in p.rules[2:])


def test_changed_config_or_missing_admission_fails_before_staging(setup):
    s = setup
    with pytest.raises(m.OwnerInstallError, match="member_admission_is_part"):
        m.prepare(s.plan_file, home=s.home, holders=(HOLDER,), now=1000)
    assert not (s.base / "wallet-endpoints").exists()
    store(s.base / "local.json", {"changed": True})
    with pytest.raises(m.OwnerInstallError, match="instance_or_plan_changed"):
        prepare(s)
    assert not (s.base / "wallet-endpoints").exists()


def test_changed_digest_or_stage_never_dispatches_native_provision(setup):
    s = setup
    out = prepare(s)
    with pytest.raises(m.OwnerInstallError, match="review_changed"):
        m.activate(s.plan_file, "00" * 32, home=s.home)
    root = Path(out["review"]["root"])
    (root / "authority/operators.json").write_bytes(b"{}")
    with pytest.raises(m.OwnerInstallError, match="staged_input_changed"):
        m.activate(s.plan_file, out["review_digest"], home=s.home)
    assert all(row[2] == "status" for row in s.events)


def test_linked_plan_or_artifact_rejected_without_staging(setup):
    s = setup
    link = s.base / "linked.json"
    link.symlink_to(s.plan_file)
    with pytest.raises(m.OwnerInstallError, match="unsafe_path"):
        m.prepare(link, home=s.home, now=1000)
    binary = s.base / "candidates/secs-devgraph-work-v2"
    binary.write_bytes(b"tampered")
    with pytest.raises(m.OwnerInstallError, match="artifact_changed"):
        prepare(s)


def test_uncertain_native_activation_is_retained_and_never_retried(setup, monkeypatch):
    s = setup
    from tests.ops.test_owner_admin_upgrade import authorize_local_upgrade

    out = authorize_local_upgrade(s, monkeypatch)

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def connect_ex(self, *_):
            return 1

    monkeypatch.setattr(m.socket, "socket", lambda *a, **kw: Socket())
    original = m.invoke

    def fail(args, **kwargs):
        if args[2] == "provision":
            raise m.OwnerInstallError("uncertain_native_result")
        return original(args, **kwargs)

    monkeypatch.setattr(m, "invoke", fail)
    with pytest.raises(m.OwnerInstallError, match="uncertain_native_result"):
        m.activate(s.plan_file, out["review_digest"], home=s.home)
    status = m.active_status(s.plan_file, home=s.home)
    assert "activation-started" in status["phases"] and not status["active"]
    with pytest.raises(m.OwnerInstallError, match="prior_activation_requires_reconciliation"):
        m.activate(s.plan_file, out["review_digest"], home=s.home)


def test_successful_cutover_keeps_graph_key_and_resource_identity(setup, monkeypatch):
    import http.client

    s = setup
    from tests.ops.test_owner_admin_upgrade import authorize_local_upgrade

    out = authorize_local_upgrade(s, monkeypatch)
    root = Path(out["review"]["root"])

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def connect_ex(self, *_):
            return 1

    class Response:
        status = 200

        def read(self, *_):
            value = json.loads((root / "connection.json").read_bytes())
            value.pop("network")
            value.pop("tls_certificate_sha256")
            return m.encoded(value)

    class Connection:
        def __init__(self, *_, **kwargs):
            pass

        def request(self, *_):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(m.socket, "socket", lambda *a, **kw: Socket())
    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)

    def launch(args, **kwargs):
        assert args[:2] == ["/bin/launchctl", "bootstrap"]
        assert str(s.home) in args[-1]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(m.subprocess, "run", launch)
    result = m.activate(s.plan_file, out["review_digest"], home=s.home)
    assert result["active"] and result["member_grants_created"] == 0
    assert (
        result["resource_id"] == s.plan["resource_id"]
        and result["owner_wallet_confirmation_required"]
    )
    assert result["native_key_unchanged"] and not result["database_restarted"]
    record = json.loads((s.base / "resource-registration.json").read_bytes())
    assert (
        record["resource_id"] == s.plan["resource_id"]
        and record["endpoint"] == s.plan["local_endpoint"]
    )
    services = list((s.home / ".zenith/services/v1").glob("*.json"))
    assert len(services) == 1
    published = json.loads(services[0].read_bytes())
    assert (
        published["resource_id"] == s.plan["resource_id"]
        and published["endpoint"] == s.plan["wallet_endpoint"]
    )
    assert published["instance_id"] != published["resource_id"]
    assert record["connection"] is None
    assert json.loads((s.base / "local.json").read_bytes()) == {"instance": "fixture"}
    assert (
        json.loads(
            (
                s.data / "secrets/secs-magik/devgraph.work.v1/secs-public-key-registry.json"
            ).read_bytes()
        )
        == s.registry
    )
    assert m.active_status(s.plan_file, home=s.home)["active"]
    with pytest.raises(m.OwnerInstallError, match="prior_activation_requires_reconciliation"):
        m.activate(s.plan_file, out["review_digest"], home=s.home)


def test_review_fixture_export(setup):
    destination = os.environ.get("OWNER_REVIEW_FIXTURE")
    if not destination:
        return
    s = setup
    result = prepare(s)
    Path(destination).write_bytes(m.encoded(dict(plan=s.plan, **result)))


def test_connection_publication_is_compare_and_swap_and_keeps_instance_id(tmp_path):
    import hashlib

    from tests.ops.test_local_resources import FIXTURES, instance

    from devgraph.local_resources import LocalResourceError, register_resource

    home, config = instance(tmp_path)
    first = register_resource(config, home=home)
    state = config.with_name("resource-registration.json")
    expected = hashlib.sha256(state.read_bytes()).hexdigest()
    profile = tmp_path / "connection.json"
    profile.write_text(
        json.dumps(
            json.loads((FIXTURES / "zenith-local-resource-example.json").read_text())["connection"]
        )
    )
    before = state.read_bytes()
    with pytest.raises(LocalResourceError):
        register_resource(
            config,
            home=home,
            connection_file=profile,
            replace=True,
            expected_registration_sha256="0" * 64,
        )
    assert state.read_bytes() == before
    after = register_resource(
        config,
        home=home,
        connection_file=profile,
        replace=True,
        expected_registration_sha256=expected,
    )
    assert after["resource_id"] == first["resource_id"]


def test_preparation_cannot_bind_a_later_changed_instance(setup, monkeypatch):
    s = setup

    def copy(_source, destination):
        store(destination / "__init__.py", b"# fixture")
        store(s.base / "local.json", {"concurrently_changed": True})

    monkeypatch.setattr(m, "copy_package", copy)
    with pytest.raises(m.OwnerInstallError, match="instance_changed_during_preparation"):
        prepare(s)
    assert not any(event[2] == "provision" for event in s.events)
