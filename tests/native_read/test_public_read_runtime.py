import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devgraph.public_read_runtime import PublicReadBoundary, public_origin


def boundary():
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "DELETE", "PUT", "PATCH"])
    async def reached(path: str):
        return {"downstream": True}

    return PublicReadBoundary(
        app, origin="https://remote.example.test", expires_at=int(time.time()) + 600
    )


def test_only_closed_read_and_member_request_routes_reach_the_app():
    with TestClient(boundary(), base_url="https://remote.example.test") as client:
        for path in ["/monitor", "/.well-known/devgraph-connection.json"]:
            assert client.get(path).json() == {"downstream": True}
        for path in [
            "/devgraph/native-read/v2/enrollments",
            "/monitor/wallet-access/v1/requests",
            "/devgraph/access-requests/v2/actions",
        ]:
            assert client.post(path, json={}).json() == {"downstream": True}
        for path in [
            "/monitor/access/",
            "/devgraph/access-operator/v1/challenges",
            "/devgraph/access-operator/v1/actions",
            "/credential-work/v2",
            "/work-operations/v1",
            "/work/Task/x",
            "/query/cypher",
            "/docs",
            "/openapi.json",
        ]:
            assert client.get(path).status_code == 404
            assert client.post(path, json={}).status_code == 404
        assert client.delete("/monitor").status_code == 404


def test_forwarded_headers_cannot_authorize_wrong_host_cleartext_or_other_origin():
    with TestClient(boundary(), base_url="http://remote.example.test") as client:
        assert client.get("/monitor", headers={"X-Forwarded-Proto": "https"}).status_code == 421
    with TestClient(boundary(), base_url="https://remote.example.test") as client:
        assert (
            client.get(
                "/monitor",
                headers={"Host": "wrong.example.test", "X-Forwarded-Host": "remote.example.test"},
            ).status_code
            == 421
        )
        assert (
            client.post(
                "/devgraph/access-requests/v2/actions",
                json={},
                headers={"Origin": "https://other.test"},
            ).status_code
            == 421
        )
        assert (
            client.get("/monitor", headers={"Authorization": "Bearer synthetic"}).status_code == 421
        )


def test_expiry_body_encoded_path_query_rate_and_concurrency_fail_before_dispatch():
    app = boundary()
    with TestClient(app, base_url="https://remote.example.test") as client:
        assert (
            client.post("/devgraph/access-requests/v2/actions", content=b"x" * 65537).status_code
            == 413
        )
        assert client.get("/%6donitor").status_code == 400
        assert client.post("/devgraph/access-requests/v2/actions?x=1", json={}).status_code == 400
        app.active = 8
        assert client.get("/monitor").status_code == 429
        app.active = 0
        app.tokens = 0
        app.last = time.monotonic()
        assert client.get("/monitor").status_code == 429
        app.expires_at = 0
        assert client.get("/monitor").status_code == 503


@pytest.mark.parametrize(
    "value",
    [
        "http://remote.test",
        "https://user:password@remote.test",
        "https://remote.test/",
        "https://remote.test?secret=x",
        "https://remote.test.",
        "https://REMOTE.test",
    ],
)
def test_public_origin_is_explicit_and_canonical(value):
    with pytest.raises(ValueError):
        public_origin(value)


def test_operator_routes_require_separate_opt_in_and_close_on_policy_change():
    app = boundary()
    approved = [True]
    app.approved_operator_policy = lambda: approved[0]
    with TestClient(app, base_url="https://remote.example.test") as client:
        assert client.post("/devgraph/access-operator/v1/actions", json={}).json() == {
            "downstream": True
        }
        assert client.get("/monitor/access/").json() == {"downstream": True}
        approved[0] = False
        assert client.post("/devgraph/access-operator/v1/actions", json={}).status_code == 503
        assert client.get("/monitor/access/").status_code == 503
        assert client.post("/work-operations/v1", json={}).status_code == 404
