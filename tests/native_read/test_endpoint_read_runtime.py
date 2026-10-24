from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devgraph.endpoint_read_runtime import EndpointReadBoundary

ORIGINS = (
    "https://host.example.ts.net:9443",
    "https://public.example.test",
    "https://192.168.50.10:18891",
)


@pytest.mark.parametrize("origin", ORIGINS)
def test_member_entry_point_keeps_operator_and_private_routes_closed(origin):
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    def guarded(path: str):
        return {"downstream": path}

    boundary = EndpointReadBoundary(app, origin=origin)
    with TestClient(boundary, base_url=origin) as client:
        assert client.get("/monitor").status_code == 200
        assert client.get("/devgraph/access-requests/v2/catalog").status_code == 200
        for path in (
            "/devgraph/access-requests/v2/challenges",
            "/devgraph/access-requests/v2/actions",
            "/monitor/wallet-access/v1/enrollments",
            "/devgraph/native-read/v2/requests",
        ):
            assert client.post(path, json={}).status_code == 200
        for path in (
            "/monitor/access/",
            "/devgraph/access-operator/v1/actions",
            "/credential-work/v2/execute",
            "/work-operations/v2",
            "/work/Task/private",
            "/query/cypher",
            "/docs",
        ):
            assert client.get(path).status_code == 404
            assert client.post(path, json={}).status_code == 404
        for headers in (
            {"Host": "other.test"},
            {"Origin": "https://other.test"},
            {"Authorization": "Bearer cannot-grant-access"},
        ):
            assert client.post(
                "/devgraph/access-requests/v2/actions", json={}, headers=headers
            ).status_code == 421
        assert client.post(
            "/devgraph/access-requests/v2/actions", content=b"x" * 65537
        ).status_code == 413
        assert not boundary.expired()
    with TestClient(boundary, base_url=origin.replace("https:", "http:")) as client:
        assert client.get("/", headers={"X-Forwarded-Proto": "https"}).status_code == 421


@pytest.mark.parametrize("origin", ORIGINS)
def test_real_holder_signed_request_is_pending_and_retry_keeps_original(origin, tmp_path):
    from tests.native_read.test_access_operator import signed
    from tests.native_read.test_access_requests import action, setup

    from devgraph.api.access_requests import register_access_request_routes
    from devgraph.auth.access_requests import AccessRequestInbox

    old, holder, public, proof, _ = setup(tmp_path)
    inbox = AccessRequestInbox(
        path=tmp_path / "endpoint-inbox.sqlite",
        issuer=old.issuer,
        receiver=origin,
        membership_check=old.membership_check,
    )
    app = FastAPI()
    register_access_request_routes(app, inbox)
    with TestClient(EndpointReadBoundary(app, origin=origin), base_url=origin) as client:
        request = action(public).model_dump(mode="json", by_alias=True)
        response = client.post("/devgraph/access-requests/v2/challenges", json={"request": request})
        assert response.status_code == 200
        challenge = response.json()
        assert challenge["receiver"] == origin
        approval = dict(challenge=challenge, proof=proof, signature=signed(holder, challenge))
        response = client.post("/devgraph/access-requests/v2/actions", json=approval)
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "pending"
        assert result["authority_issued"] is False
        assert client.post("/devgraph/access-requests/v2/actions", json=approval).json() == result
        assert inbox.pending()["total"] == 1
        forged = dict(approval, signature="A" * 86)
        assert client.post("/devgraph/access-requests/v2/actions", json=forged).status_code == 401


@pytest.fixture
def configured(monkeypatch):
    import devgraph.endpoint_read_runtime as runtime
    import devgraph.signed_read_runtime as signed

    origin = ORIGINS[0]
    fields = (
        "member_policy_file", "access_inbox_database_file", "access_operator_policy_file",
        "write_native_policy_file", "enrollment_policy_file", "authority_policy_file",
        "membership_trust_file", "revoked_memberships_file", "enrollment_database_file",
        "secs_binary", "issuer_key_file",
    )
    values = {
        "schema": "devgraph.signed-read-runtime.v8",
        "membership_requirement": "optional",
        "secs_binary_sha256": "a" * 64,
        **{key: "/synthetic/" + key for key in fields},
    }
    authority = dict(
        issuer="fixture", receiver=origin, key_id="fixture", public_key="b" * 64,
        audience="devgraph",
    )
    data = {
        Path("/synthetic/runtime"): values,
        Path(values["enrollment_policy_file"]): dict(receiver=origin, issuer="fixture"),
        Path(values["authority_policy_file"]): authority,
        Path(values["access_operator_policy_file"]): {
            "ceiling": dict(receiver=origin, issuer="fixture")
        },
    }
    app = FastAPI()
    app.state.access_requests_enabled = True
    app.state.access_operator_enabled = True
    monkeypatch.setenv("DEVGRAPH_SIGNED_READ_CONFIG", "/synthetic/runtime")
    monkeypatch.setenv("DEVGRAPH_ENDPOINT_ORIGIN", origin)
    monkeypatch.setattr(runtime, "private_json", data.__getitem__)
    monkeypatch.setattr(signed, "create_app", lambda: app)
    return runtime, origin, data, app


def test_factory_preserves_public_pins_and_client_route_choice(configured):
    runtime, origin, data, app = configured
    with TestClient(runtime.create_app(), base_url=origin) as client:
        profile = client.get("/.well-known/devgraph-connection.json").json()
        assert profile["receiver"] == origin
        assert profile["authority"] == data[Path("/synthetic/authority_policy_file")]
        assert "network" not in profile
        assert client.get("/live").json()["credential_requests_enabled"] is True
    assert app.state.access_operator_enabled is False


@pytest.mark.parametrize("field", (
    "enrollment_policy_file", "authority_policy_file", "access_operator_policy_file",
))
def test_receiver_mismatch_is_reviewed_and_cannot_start(configured, field):
    runtime, origin, data, _ = configured
    policy = data[Path("/synthetic/" + field)]
    if field == "access_operator_policy_file":
        policy = policy["ceiling"]
    policy["receiver"] = "https://127.0.0.1:58443"
    result = runtime.review_endpoint("/synthetic/runtime", origin)
    assert result["blockers"] and not result["ready_for_startup_validation"]
    assert result["authority_changed"] is False and result["grants_created"] == 0
    with pytest.raises(ValueError, match="must match before startup"):
        runtime.create_app()


def test_factory_does_not_claim_requests_without_the_inbox(configured):
    runtime, _, _, app = configured
    app.state.access_requests_enabled = False
    with pytest.raises(ValueError, match="request routes are required"):
        runtime.create_app()


def test_legacy_no_inbox_configuration_is_rejected_before_composition(configured):
    runtime, origin, data, _ = configured
    values = data[Path("/synthetic/runtime")]
    values["schema"] = "devgraph.signed-read-runtime.v1"
    values.pop("access_inbox_database_file")
    values.pop("access_operator_policy_file")
    review = runtime.review_endpoint("/synthetic/runtime", origin)
    assert len(review["blockers"]) == 2
    with pytest.raises(ValueError, match="must match before startup"):
        runtime.create_app()
