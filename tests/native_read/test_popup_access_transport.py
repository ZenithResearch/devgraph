"""Popup transport never substitutes for downstream signed-proof admission."""

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse
from tests.native_read.test_public_read_runtime import boundary

from devgraph.public_read_runtime import PublicReadBoundary

PATHS = (
    "/monitor/wallet-access/v1/challenges",
    "/monitor/wallet-access/v1/grants/v2",
    "/devgraph/access-requests/v2/challenges",
    "/devgraph/access-requests/v2/actions",
)
EXTENSIONS = (
    "chrome-extension://" + "a" * 32,
    "moz-extension://12345678-1234-1234-1234-123456789abc",
    "safari-web-extension://12345678-1234-1234-1234-123456789abc",
)


@pytest.mark.parametrize("origin", EXTENSIONS)
@pytest.mark.parametrize("path", PATHS)
def test_popup_preflight_and_post_reach_only_the_existing_signed_handler(origin, path):
    with TestClient(boundary(), base_url="https://remote.example.test") as client:
        preflight = client.options(
            path,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.status_code == 204
        assert preflight.headers["access-control-allow-origin"] == origin
        assert "access-control-allow-credentials" not in preflight.headers
        result = client.post(path, json={}, headers={"Origin": origin})
        assert result.json() == {"downstream": True}
        assert result.headers["access-control-allow-origin"] == origin
        assert result.headers["cache-control"] == "no-store"
        assert "Origin" in result.headers["vary"]


@pytest.mark.parametrize(
    "origin",
    (
        "https://other.test",
        "null",
        "chrome-extension://" + "a" * 31,
        "chrome-extension://" + "a" * 32 + "/path",
        "chrome-extension://" + "a" * 32 + ":443",
        "chrome-extension://user@" + "a" * 32,
    ),
)
def test_other_origins_cannot_use_popup_transport(origin):
    with TestClient(boundary(), base_url="https://remote.example.test") as client:
        for path in PATHS:
            response = client.post(path, json={}, headers={"Origin": origin})
            assert response.status_code == 421
            assert "access-control-allow-origin" not in response.headers


def test_popup_transport_does_not_expose_reads_operator_v1_grants_or_arbitrary_routes():
    with TestClient(boundary(), base_url="https://remote.example.test") as client:
        assert (
            client.get("/monitor/wallet-access/v1", headers={"Origin": EXTENSIONS[0]}).status_code
            == 421
        )
        for path in (
            "/monitor/wallet-access/v1/requests",
            "/monitor/wallet-access/v1/grants",
            "/monitor/wallet-access/v1/enrollments",
            "/devgraph/access-operator/v1/actions",
            "/work-operations/v1",
            "/query/cypher",
            "/monitor",
        ):
            response = client.post(path, json={}, headers={"Origin": EXTENSIONS[0]})
            assert response.status_code in (404, 421)
            assert "access-control-allow-origin" not in response.headers


def test_preflight_host_query_method_headers_and_ambient_credentials_remain_closed():
    with TestClient(boundary(), base_url="https://remote.example.test") as client:
        base = {"Origin": EXTENSIONS[0], "Access-Control-Request-Method": "POST"}
        for extra in (
            {"Access-Control-Request-Method": "DELETE"},
            {"Access-Control-Request-Headers": "authorization"},
            {"Access-Control-Request-Headers": "content-type,x-unreviewed"},
        ):
            assert client.options(PATHS[0], headers={**base, **extra}).status_code == 400
        assert client.options(PATHS[0] + "?query=1", headers=base).status_code == 400
        assert client.options(PATHS[0], headers={**base, "Host": "other.test"}).status_code == 421
        for extra in ({"Cookie": "session=synthetic"}, {"Authorization": "Bearer synthetic"}):
            assert client.post(PATHS[0], json={}, headers={**base, **extra}).status_code == 421


def test_canonical_prefix_and_original_body_limits_are_preserved():
    target = boundary()
    target.canonical_paths = True
    with TestClient(target, base_url="https://remote.example.test") as client:
        preflight = client.options(
            "/service/v1" + PATHS[1],
            headers={
                "Origin": EXTENSIONS[0],
                "Access-Control-Request-Method": "POST",
            },
        )
        assert preflight.status_code == 204
        response = client.post("/service/v1" + PATHS[1], json={}, headers={"Origin": EXTENSIONS[0]})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == EXTENSIONS[0]
        assert (
            client.post(
                PATHS[1],
                content=b"x" * 65537,
                headers={"Origin": EXTENSIONS[0], "Content-Type": "application/json"},
            ).status_code
            == 413
        )


def test_controlled_denials_keep_status_and_cors_without_cookies_or_caching():
    app = FastAPI()

    @app.post(PATHS[1])
    def denied():
        return JSONResponse(
            {"error": "proof_required"},
            status_code=401,
            headers={
                "Set-Cookie": "ambient=synthetic",
                "Access-Control-Allow-Credentials": "true",
            },
        )

    target = PublicReadBoundary(
        app, origin="https://remote.example.test", expires_at=time.time() + 600
    )
    with TestClient(target, base_url="https://remote.example.test") as client:
        response = client.post(PATHS[1], json={}, headers={"Origin": EXTENSIONS[0]})
        assert response.status_code == 401
        assert response.json() == {"error": "proof_required"}
        assert response.headers["access-control-allow-origin"] == EXTENSIONS[0]
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        assert "access-control-allow-credentials" not in response.headers


def test_preflight_never_reaches_the_app_and_remains_rate_expiry_and_tls_bounded():
    calls = []
    app = FastAPI()

    @app.api_route(PATHS[0], methods=["POST", "OPTIONS"])
    def reached():
        calls.append(True)
        raise AssertionError("preflight must not dispatch")

    target = PublicReadBoundary(
        app, origin="https://remote.example.test", expires_at=time.time() + 600
    )
    headers = {"Origin": EXTENSIONS[0], "Access-Control-Request-Method": "POST"}
    with TestClient(target, base_url="https://remote.example.test") as client:
        assert client.options(PATHS[0], headers=headers).status_code == 204
        assert (
            client.options(PATHS[0], headers={**headers, "X-Long": "x" * 16385}).status_code == 400
        )
        assert client.request("OPTIONS", PATHS[0], headers=headers, content=b"x").status_code == 413
        target.active = 8
        assert client.options(PATHS[0], headers=headers).status_code == 429
        target.active = 0
        target.tokens = 0
        target.last = time.monotonic()
        assert client.options(PATHS[0], headers=headers).status_code == 429
        target.expires_at = 0
        assert client.options(PATHS[0], headers=headers).status_code == 503
    target.expires_at = time.time() + 600
    with TestClient(target, base_url="http://remote.example.test") as client:
        assert client.options(PATHS[0], headers=headers).status_code == 421
    assert not calls


def test_extension_transport_cannot_reenable_an_explicitly_disabled_post_route():
    target = boundary()
    target.post_paths = frozenset()
    with TestClient(target, base_url="https://remote.example.test") as client:
        for path in PATHS:
            response = client.post(path, json={}, headers={"Origin": EXTENSIONS[0]})
            assert response.status_code == 421
            assert "access-control-allow-origin" not in response.headers


def test_duplicate_headers_encoded_aliases_and_non_json_posts_do_not_dispatch():
    with TestClient(boundary(), base_url="https://remote.example.test") as client:
        origin = ("Origin", EXTENSIONS[0])
        assert client.post(PATHS[0], json={}, headers=[origin, origin]).status_code == 421
        assert (
            client.post(
                PATHS[0],
                json={},
                headers=[origin, ("Host", "remote.example.test"), ("Host", "remote.example.test")],
            ).status_code
            == 421
        )
        assert (
            client.options(
                PATHS[0],
                headers=[
                    origin,
                    ("Access-Control-Request-Method", "POST"),
                    ("Access-Control-Request-Method", "POST"),
                ],
            ).status_code
            == 400
        )
        assert (
            client.options(
                PATHS[0],
                headers=[
                    origin,
                    ("Access-Control-Request-Method", "POST"),
                    ("Access-Control-Request-Headers", "content-type"),
                    ("Access-Control-Request-Headers", "content-type"),
                ],
            ).status_code
            == 400
        )
        assert (
            client.post(
                PATHS[0].replace("challenges", "%63hallenges"), json={}, headers=[origin]
            ).status_code
            == 400
        )
        assert (
            client.post(
                PATHS[0], content="{}", headers=[origin, ("Content-Type", "text/plain")]
            ).status_code
            == 400
        )
