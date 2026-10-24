import hashlib
import json
from pathlib import Path

import pytest
from tests.native_read.test_owner_https_runtime import endpoint
from tests.ops.test_owner_install import setup as setup

from devgraph.auth.delegated_contract import canonical_json
from devgraph.local_services import instance_id
from devgraph.ops.local_admin_rpc import selected


def binding(s, monkeypatch):
    config, profile = endpoint(s, monkeypatch, "https://graph.example.ts.net:9443")
    monkeypatch.setattr(Path, "home", lambda: s.home)
    review = json.loads(config.owner_install_review_file.read_text())
    identity = instance_id(review["resource_id"], "wallet-api", profile["receiver"])
    runtime = s.store(
        s.base / "secondary-runtime.json",
        config.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    c = dict(
        schema="secs.local-admin-config.v3",
        resource_id=review["resource_id"],
        instance_id=identity,
        receiver=profile["receiver"],
        issuer=profile["issuer"],
        profile_sha256=hashlib.sha256(canonical_json(profile)).hexdigest(),
        runtime=str(runtime),
    )
    request = dict(
        c, schema="secs.local-admin-request.v1", request_id="a" * 32, operation="inspect", body={}
    )
    request.pop("runtime")
    service = dict(
        schema="zenith.local-service.v1",
        resource_id=c["resource_id"],
        instance_id=identity,
        kind="devgraph",
        interface="wallet-api",
        name=profile["name"],
        endpoint=profile["receiver"],
        connection=profile,
        updated_at=1000,
    )
    path = s.store(s.home / ".zenith/services/v1" / (identity + ".json"), service)
    return c, request, path


def test_secondary_rpc_requires_current_exact_registered_profile(setup, monkeypatch):
    c, request, path = binding(setup, monkeypatch)
    selected(c, request)
    value = json.loads(path.read_text())
    value["connection"]["name"] = value["name"] = "Changed public profile"
    setup.store(path, value)
    with pytest.raises(ValueError, match="registered secondary"):
        selected(c, request)
    path.unlink()
    with pytest.raises(OSError):
        selected(c, request)


@pytest.mark.parametrize(
    "field", ("resource_id", "instance_id", "receiver", "issuer", "profile_sha256")
)
def test_secondary_rpc_envelope_cannot_select_another_binding(setup, monkeypatch, field):
    c, request, _ = binding(setup, monkeypatch)
    request[field] = "changed"
    with pytest.raises(ValueError, match="selected receiver"):
        selected(c, request)


def test_v3_rejects_original_v9_runtime_even_if_its_owner_is_valid(setup, monkeypatch):
    c, request, _ = binding(setup, monkeypatch)
    runtime = json.loads(Path(c["runtime"]).read_text())
    c["runtime"] = runtime["owner_parent_runtime_file"]
    with pytest.raises(ValueError, match="published owner runtime"):
        selected(c, request)
