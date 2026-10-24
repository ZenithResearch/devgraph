import copy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.ops.test_owner_install import setup as setup
from tests.ops.test_service_owner import authorize

from devgraph.owner_endpoint import endpoint_operator_policy
from devgraph.owner_lan_runtime import OwnerLANBoundary
from devgraph.signed_read_runtime import ReadRuntimeConfiguration, owner_operator_policy

ORIGIN = "https://192.168.1.77:58754"


def publication(s, monkeypatch):
    _, _, prepared = authorize(s, monkeypatch)
    parent = Path(prepared["preparation"]["review"]["root"]) / "runtime.json"
    value = json.loads(parent.read_text())
    base = s.base / "published"
    value["schema"] = "devgraph.signed-read-runtime.v10"
    value["owner_parent_runtime_file"] = str(parent)
    profile = copy.deepcopy(prepared["preparation"]["review"]["connection"])
    profile.update(receiver=ORIGIN, network="lan", tls_certificate_sha256="a" * 64)
    profile["authority"]["receiver"] = ORIGIN
    value["owner_endpoint_profile_file"] = str(s.store(base / "profile.json", profile))
    for field in ["enrollment_policy_file", "authority_policy_file", "access_operator_policy_file"]:
        original = json.loads(Path(value[field]).read_text())
        if field == "access_operator_policy_file":
            original["ceiling"]["receiver"] = ORIGIN
        else:
            original["receiver"] = ORIGIN
        value[field] = str(s.store(base / (field + ".json"), original))
    for field in [
        "enrollment_database_file",
        "access_inbox_database_file",
        "member_policy_file",
        "owner_native_admission_directory",
    ]:
        value[field] = str(base / field)
    return ReadRuntimeConfiguration.model_validate(value), profile


def test_preserves_actual_signed_owner_and_bounds_not_self_registration(setup, monkeypatch):
    cfg, profile = publication(setup, monkeypatch)
    result = endpoint_operator_policy(cfg, data_root=setup.data)
    parent = ReadRuntimeConfiguration.model_validate(
        json.loads(cfg.owner_parent_runtime_file.read_text())
    )
    original = owner_operator_policy(parent, data_root=setup.data)
    expected = copy.deepcopy(original)
    expected["ceiling"]["receiver"] = ORIGIN
    assert result == expected
    # Publication never inserts a second owner grant or modifies original owner policy.
    assert owner_operator_policy(parent, data_root=setup.data) == original
    assert result["ceiling"]["receiver"] == profile["receiver"]
    with pytest.raises(ValueError):
        endpoint_operator_policy(cfg, data_root=Path("/another-graph"))
    original["operator_public_keys"] = ["b" * 64]
    setup.store(parent.access_operator_policy_file, original)
    with pytest.raises(ValueError):
        endpoint_operator_policy(cfg, data_root=setup.data)


@pytest.mark.parametrize(
    "change", ["operator", "write", "read", "profile", "state", "parent", "issuer"]
)
def test_rejects_widening_rebinding_shared_state_and_chained_publication(
    setup, monkeypatch, change
):
    cfg, _ = publication(setup, monkeypatch)
    if change in ("operator", "write"):
        v = json.loads(cfg.access_operator_policy_file.read_text())
        if change == "operator":
            v["operator_public_keys"].append("b" * 64)
        else:
            v["ceiling"]["write_grants"] = []
        setup.store(cfg.access_operator_policy_file, v)
    elif change == "read":
        v = json.loads(cfg.enrollment_policy_file.read_text())
        v["lifetime_seconds"] += 1
        setup.store(cfg.enrollment_policy_file, v)
    elif change == "profile":
        v = json.loads(cfg.owner_endpoint_profile_file.read_text())
        v["receiver"] = "https://8.8.8.8:58754"
        setup.store(cfg.owner_endpoint_profile_file, v)
    elif change == "state":
        original = json.loads(cfg.owner_parent_runtime_file.read_text())
        cfg = cfg.model_copy(
            update={"access_inbox_database_file": Path(original["access_inbox_database_file"])}
        )
    elif change == "parent":
        v = json.loads(cfg.owner_parent_runtime_file.read_text())
        v["schema"] = "devgraph.signed-read-runtime.v10"
        setup.store(cfg.owner_parent_runtime_file, v)
    elif change == "issuer":
        cfg = cfg.model_copy(update={"issuer_key_file": Path("/another/key")})
    with pytest.raises(ValueError):
        endpoint_operator_policy(cfg, data_root=setup.data)


def test_lan_never_exposes_unsigned_operator_capacity_or_browser_admin():
    app = FastAPI()

    @app.post("/{path:path}", status_code=401)
    def guarded(path: str):
        return {"signature_required": True}

    with TestClient(OwnerLANBoundary(app, origin=ORIGIN), base_url=ORIGIN) as c:
        for route in [
            "/devgraph/access-operator/v1/challenges",
            "/devgraph/access-operator/v1/actions",
            "/monitor/access/",
            "/query",
            "/work-operations/v1",
        ]:
            assert c.post(route, json={}).status_code == 404
            assert c.get(route).status_code == 404
        assert c.post("/devgraph/access-requests/v2/actions", json={}).status_code == 401
        assert c.post("/credential-work/v2/execute", json={}).status_code == 401
        assert (
            c.post(
                "/credential-work/v2/execute", json={}, headers={"Host": "evil.example"}
            ).status_code
            == 421
        )
