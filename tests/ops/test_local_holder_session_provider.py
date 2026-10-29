"""New member-session registration is explicit and never starts the old owner path."""

import io
import json
from types import SimpleNamespace

import pytest

from devgraph import local_holder_session_provider as module
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.holder_sessions import HolderSessionPolicy
from devgraph.holder_session_provider import serve


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    root = tmp_path / "source"
    (root / "src/devgraph").mkdir(parents=True)
    (root / "scripts").mkdir()
    for p in (
        root / "src/devgraph/one.py",
        root / "scripts/holder_session_provider.py",
        root / "uv.lock",
    ):
        p.write_text("fixture\n")
    files = {
        name: tmp_path / name
        for name in ("host", "runtime", "agent", "python", "policy", "authority", "holder")
    }
    for p in files.values():
        p.write_text("{}")
        p.chmod(0o600)
    pin = {
        "issuer": "fixture",
        "key_id": "fixture",
        "public_key": "ab" * 32,
        "service": "service:fixture",
    }
    context = {"origin": "app://zenith-transfer", "audience": "devgraph"}
    policy = HolderSessionPolicy(enabled=True, allowed_origins=[context["origin"]])
    config = SimpleNamespace(
        holder_session_policy_file=files["policy"],
        authority_policy_file=files["authority"],
        holder_grant_binary=files["holder"],
    )
    monkeypatch.setattr(module, "configured_provider", lambda _: (config, pin, policy))
    monkeypatch.setattr(module, "load_local_config", lambda _: SimpleNamespace(data_root=tmp_path))

    def forbidden(**kwargs):
        raise AssertionError("metadata preparation must not start a runtime")

    monkeypatch.setattr("devgraph.signed_read_runtime.create_app", forbidden)
    return SimpleNamespace(
        files=files, root=root, pin=pin, context=context, directory=tmp_path / "review"
    )


def prepare(s):
    return module.prepare_registration(
        host_config=s.files["host"],
        runtime_config=s.files["runtime"],
        agent_binary=s.files["agent"],
        interpreter=s.files["python"],
        source_root=s.root,
        directory=s.directory,
        context=s.context,
    )


def test_registration_and_agent_profile_are_separate_create_only_application_configuration(setup):
    path = prepare(setup)
    descriptor = json.loads(path.read_bytes())
    profile = json.loads((path.parent / "profile.json").read_bytes())
    agent = json.loads((path.parent / "agent.json").read_bytes())
    assert descriptor["schema"] == "zenith.holder-session-provider-registration.v1"
    assert descriptor["provider"] == agent["provider"] == setup.pin
    assert agent == {
        "schema": "castalia.holder-session-agent-profile.v1",
        "provider": setup.pin,
        "context": setup.context,
        "maximum_session_seconds": 300,
    }
    assert profile["schema"] == "devgraph.holder-session-provider-profile.v1"
    assert "receiver_pin" not in descriptor and "session_binary" not in profile
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert not list(path.parent.glob("*.sqlite"))
    with pytest.raises(FileExistsError):
        prepare(setup)
    setup.files["runtime"].write_text('{"changed":true}')
    with pytest.raises(ValueError):
        module.validate_profile(profile)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "disposable"),
        ("schema", "devgraph.local-session-provider.v1"),
        ("context", {"origin": "https://other.example", "audience": "devgraph"}),
    ],
)
def test_no_old_profile_or_caller_selected_origin_fallback(setup, field, value):
    path = prepare(setup)
    profile = json.loads((path.parent / "profile.json").read_bytes())
    profile[field] = value
    with pytest.raises(ValueError):
        module.validate_profile(profile)


def test_private_wire_redacts_errors_preserves_correlation_and_closes_on_eof():
    from tests.native_read.test_holder_session_provider import command

    events = []
    app = SimpleNamespace(
        current=lambda: None,
        services=lambda: {"public": True},
        close=lambda: events.append("closed"),
    )
    values = [command("services", {}), command("owner_challenge", {"secret": "never echo"})]
    sink = io.BytesIO()
    assert (
        serve(
            app, source=io.BytesIO(b"".join(canonical_json(v) + b"\n" for v in values)), sink=sink
        )
        == 0
    )
    results = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert results[0]["value"] == {"public": True}
    assert results[1] == {
        "schema": "devgraph.holder-session-provider-result.v1",
        "request_id": "ab" * 16,
        "ok": False,
        "error": "holder_session_provider_denied_or_outcome_unknown",
    }
    assert "never echo" not in sink.getvalue().decode()
    assert events == ["closed"]


@pytest.mark.parametrize("raw", [b"{}", b"x" * 262145])
def test_unterminated_or_oversized_private_frames_close_without_dispatch(raw):
    events = []
    app = SimpleNamespace(close=lambda: events.append("closed"))
    sink = io.BytesIO()
    assert serve(app, source=io.BytesIO(raw), sink=sink) == 2
    assert sink.getvalue() == b"" and events == ["closed"]


@pytest.mark.parametrize("variant", ["v1-default", "required-membership", "disabled", "alias"])
def test_runtime_keeps_old_issuance_default_and_membership_but_requires_enabled_canonical_policy(
    tmp_path, monkeypatch, variant
):
    from devgraph.disposable_sessions import _write

    runtime, policy_file, authority = (
        tmp_path / name for name in ("runtime", "policy", "authority")
    )
    for path in (runtime, authority):
        _write(path, {})
    _write(
        policy_file,
        {"enabled": variant != "disabled", "allowed_origins": ["app://zenith-transfer"]},
    )
    config = SimpleNamespace(
        schema_="devgraph.signed-read-runtime.v11",
        holder_grant_binary=tmp_path / "holder",
        holder_grant_schema="secs.holder-grant.v1",
        holder_session_policy_file=policy_file,
        canonical_service_id="service:fixture",
        holder_grant_provider_runtime_file=tmp_path / "other" if variant == "alias" else runtime,
        owner_parent_runtime_file=None,
        membership_requirement="required" if variant == "required-membership" else "optional",
        authority_policy_file=authority,
    )
    authority.write_bytes(
        canonical_json({"issuer": "fixture", "key_id": "fixture", "public_key": "ab" * 32})
    )
    monkeypatch.setattr(module.ReadRuntimeConfiguration, "model_validate", lambda _: config)
    if variant in {"disabled", "alias"}:
        with pytest.raises(ValueError):
            module.configured_provider(runtime)
    else:
        selected, pin, policy = module.configured_provider(runtime)
        assert selected.holder_grant_schema == "secs.holder-grant.v1"
        assert pin["service"] == "service:fixture" and policy.enabled
