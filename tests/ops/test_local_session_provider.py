"""Registration preparation is metadata-only; no installed state is selected."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devgraph import local_session_provider as module
from devgraph.auth.delegated_contract import canonical_json


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    root = tmp_path / "source"
    (root / "src/devgraph").mkdir(parents=True)
    (root / "scripts").mkdir()
    for path in (
        root / "src/devgraph/one.py",
        root / "scripts/session_agent_service.py",
        root / "uv.lock",
    ):
        path.write_text("fixture\n")
    files = {name: tmp_path / name for name in ("host", "runtime", "helper", "python")}
    for path in files.values():
        path.write_text("{}")
        path.chmod(0o600)
    provider = dict(
        issuer="fixture", key_id="fixture-key", public_key="ab" * 32, service="service:fixture"
    )
    receiver = dict(
        issuer="fixture",
        key_id="fixture-key",
        public_key="ab" * 32,
        receiver="https://fixture.invalid",
        audience="devgraph",
    )
    monkeypatch.setattr(module, "configured_provider", lambda path: (None, provider, receiver))
    monkeypatch.setattr(
        module, "load_local_config", lambda path: SimpleNamespace(data_root=tmp_path)
    )
    import devgraph.signed_read_runtime as runtime

    def forbidden(*args, **kwargs):
        raise AssertionError("preparation must not open the runtime")

    monkeypatch.setattr(runtime, "create_app", forbidden)
    return SimpleNamespace(
        root=root, files=files, directory=tmp_path / "review", provider=provider, receiver=receiver
    )


def prepare(s):
    return module.prepare_registration(
        host_config=s.files["host"],
        runtime_config=s.files["runtime"],
        session_binary=s.files["helper"],
        interpreter=s.files["python"],
        source_root=s.root,
        directory=s.directory,
    )


def test_preparation_is_exact_private_new_files_without_starting_runtime(setup):
    descriptor = prepare(setup)
    registration = json.loads(descriptor.read_bytes())
    profile = json.loads((descriptor.parent / "profile.json").read_bytes())
    assert registration["schema"] == "zenith.session-provider-registration.v1"
    assert registration["mode"] == profile["mode"] == "production"
    assert profile["session_grant_schema"] == "secs.holder-grant.v2"
    assert registration["provider"] == setup.provider
    assert registration["receiver_pin"] == setup.receiver
    assert descriptor.stat().st_mode & 0o777 == 0o600
    assert descriptor.parent.stat().st_mode & 0o777 == 0o700
    assert len(registration["launch"]["sources"]) == 5
    assert canonical_json(profile) == (descriptor.parent / "profile.json").read_bytes()
    original = descriptor.read_bytes()
    with pytest.raises(FileExistsError):
        prepare(setup)
    assert descriptor.read_bytes() == original
    assert not list(descriptor.parent.glob("*.sqlite"))


def test_profile_pins_reject_configuration_drift_before_composition(setup):
    descriptor = prepare(setup)
    profile = json.loads((descriptor.parent / "profile.json").read_bytes())
    setup.files["runtime"].write_text('{"changed":true}')
    with pytest.raises(ValueError, match="file_changed"):
        module.validate_profile(profile)


def test_interpreter_symlink_keeps_venv_invocation_but_hashes_resolved_bytes(setup):
    import hashlib

    real = setup.files["python"].with_name("real-python")
    setup.files["python"].rename(real)
    setup.files["python"].symlink_to(real)
    descriptor = prepare(setup)
    pin = json.loads(descriptor.read_bytes())["launch"]["interpreter"]
    assert pin["path"] == str(setup.files["python"])
    assert pin["sha256"] == hashlib.sha256(real.read_bytes()).hexdigest()


def test_venv_prefix_configuration_is_pinned_when_present(setup):
    target = setup.directory.parent / "venv/bin/python"
    target.parent.mkdir(parents=True)
    setup.files["python"].rename(target)
    setup.files["python"] = target
    config = target.parent.parent / "pyvenv.cfg"
    config.write_text("include-system-site-packages = false\n")
    config.chmod(0o600)
    descriptor = prepare(setup)
    sources = json.loads(descriptor.read_bytes())["launch"]["sources"]
    assert module.file_pin(config) in sources


def test_first_party_resources_are_pinned_without_generated_cache_files(setup):
    resources = setup.root / "src/devgraph/resources"
    resources.mkdir()
    data = resources / "scope.json"
    data.write_text('{"fixture":true}')
    cache = setup.root / "src/devgraph/__pycache__"
    cache.mkdir()
    (cache / "one.pyc").write_bytes(b"not-executable")
    descriptor = prepare(setup)
    sources = json.loads(descriptor.read_bytes())["launch"]["sources"]
    assert module.file_pin(data) in sources
    assert all(not p["path"].endswith(".pyc") for p in sources)


@pytest.mark.parametrize("variant", ["required-membership", "route-alias"])
def test_profile_cannot_infer_session_eligibility_or_use_route_registry(
    tmp_path, monkeypatch, variant
):
    runtime_file = tmp_path / "runtime"
    runtime_file.write_text("{}")
    runtime_file.chmod(0o600)
    config = SimpleNamespace(
        schema_="devgraph.signed-read-runtime.v11",
        holder_grant_schema="secs.holder-grant.v2",
        holder_grant_binary=Path("/fixture/helper"),
        canonical_service_id="service:fixture",
        membership_requirement="optional",
        holder_grant_provider_runtime_file=None,
        owner_parent_runtime_file=None,
    )
    if variant == "required-membership":
        config.membership_requirement = "required"
    else:
        config.holder_grant_provider_runtime_file = tmp_path / "different-runtime"
    monkeypatch.setattr(module.ReadRuntimeConfiguration, "model_validate", lambda value: config)
    with pytest.raises(ValueError):
        module.configured_provider(runtime_file)


def test_factory_closes_composition_if_actual_provider_disagrees(setup, monkeypatch):
    descriptor = prepare(setup)
    calls = []
    storage = SimpleNamespace(close=lambda: calls.append("closed"))
    app = SimpleNamespace(
        state=SimpleNamespace(
            service_storage=storage,
            session_access_components=SimpleNamespace(
                enrollment=SimpleNamespace(
                    holder_grants=SimpleNamespace(pin=setup.provider | {"service": "service:other"})
                )
            ),
        )
    )
    import devgraph.signed_read_runtime as runtime

    monkeypatch.setattr(runtime, "create_app", lambda **kwargs: app)
    monkeypatch.setattr(module.os, "environ", dict(module.os.environ))
    with pytest.raises(ValueError, match="binding_changed"):
        module.LocalSessionApplication(descriptor.parent / "profile.json")
    assert calls == ["closed"]


def test_scoped_feature_probe_is_synthetic_and_cannot_issue_or_read_graph():
    calls = []

    def invoke(operation, **fields):
        calls.append((operation, fields))
        return {"allowed": [True]}

    module.require_scoped_authority(SimpleNamespace(service="service:fixture", invoke=invoke))
    assert len(calls) == 1 and calls[0][0] == "check_selector"
    assert set(calls[0][1]) == {"selector", "targets", "snapshot"}
    assert calls[0][1]["targets"] == [{"kind": "Arena", "id": "session-capability-probe"}]
    with pytest.raises(ValueError, match="scoped_native_authority_required"):
        module.require_scoped_authority(
            SimpleNamespace(
                service="service:fixture", invoke=lambda *args, **kw: {"allowed": [False]}
            )
        )


def test_explicit_canonical_provider_precedes_publication_parent_and_keeps_v1_default(
    tmp_path, monkeypatch
):
    runtime_file, authority_file = tmp_path / "runtime", tmp_path / "authority"
    authority = dict(
        issuer="fixture",
        key_id="fixture-key",
        public_key="ab" * 32,
        receiver="https://fixture.invalid",
        audience="devgraph",
    )
    runtime_file.write_text("{}")
    authority_file.write_bytes(canonical_json(authority))
    runtime_file.chmod(0o600)
    authority_file.chmod(0o600)
    config = SimpleNamespace(
        schema_="devgraph.signed-read-runtime.v11",
        holder_grant_schema="secs.holder-grant.v1",
        holder_grant_binary=Path("/fixture/helper"),
        canonical_service_id="service:fixture",
        membership_requirement="optional",
        holder_grant_provider_runtime_file=runtime_file,
        owner_parent_runtime_file=tmp_path / "publication-parent",
        authority_policy_file=authority_file,
    )
    monkeypatch.setattr(module.ReadRuntimeConfiguration, "model_validate", lambda value: config)
    selected, provider, receiver = module.configured_provider(runtime_file)
    assert selected.holder_grant_schema == "secs.holder-grant.v1"
    assert receiver == authority and provider["service"] == "service:fixture"
