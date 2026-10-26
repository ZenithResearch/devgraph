"""Synthetic route isolation; crypto bindings are tested in the shared Rust contract."""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from devgraph.api.canonical_service import CanonicalServiceRoute


def test_route_uses_canonical_authority_but_rejects_unadmitted_host():
    canonical = FastAPI()

    @canonical.post("/devgraph/access-requests/v2/actions")
    async def action(request: Request):
        return {"host": request.url.netloc, "body": await request.json()}

    outer = FastAPI()
    outer.mount(
        "/service/v1",
        CanonicalServiceRoute(
            canonical,
            origin="https://graph.example:9443",
            authority_receiver="https://127.0.0.1:58754",
        ),
    )
    client = TestClient(outer, base_url="https://graph.example:9443")
    body = {"original": "signed bytes are not rewritten"}
    result = client.post("/service/v1/devgraph/access-requests/v2/actions", json=body)
    assert result.status_code == 200
    assert result.json() == {"host": "127.0.0.1:58754", "body": body}
    assert (
        client.post(
            "/service/v1/devgraph/access-requests/v2/actions",
            json=body,
            headers={"host": "evil.example"},
        ).status_code
        == 421
    )
    assert (
        TestClient(outer, base_url="http://graph.example:9443")
        .post("/service/v1/devgraph/access-requests/v2/actions", json=body)
        .status_code
        == 421
    )
    assert client.get("/devgraph/access-requests/v2/actions").status_code == 404


def test_member_boundary_keeps_identical_limits_and_no_prefixed_private_access():
    from devgraph.endpoint_read_runtime import EndpointReadBoundary

    target = FastAPI()

    @target.api_route("/{path:path}", methods=["GET", "POST"])
    async def downstream(path: str):
        return {"path": path}

    boundary = EndpointReadBoundary(
        target, origin="https://graph.example:9443", canonical_paths=True
    )
    with TestClient(boundary, base_url="https://graph.example:9443") as client:
        assert client.get("/.well-known/devgraph-service.json").status_code == 200
        assert (
            client.post("/service/v1/devgraph/access-requests/v2/actions", json={}).status_code
            == 200
        )
        for path in ["/query/cypher", "/work/Task/private", "/devgraph/access-operator/v1/actions"]:
            assert client.post("/service/v1" + path, json={}).status_code == 404
        assert (
            client.post(
                "/service/v1/devgraph/access-requests/v2/actions", content=b"x" * 65537
            ).status_code
            == 413
        )
        assert (
            client.post(
                "/service/v1/devgraph/access-requests/v2/actions",
                json={},
                headers={"Host": "evil.example"},
            ).status_code
            == 421
        )


def test_two_routes_share_original_request_and_single_inbox(tmp_path):
    from tests.native_read.test_access_operator import signed
    from tests.native_read.test_access_requests import action, setup

    from devgraph.api.access_requests import register_access_request_routes

    inbox, holder, public, proof, _ = setup(tmp_path)
    canonical = FastAPI()
    register_access_request_routes(canonical, inbox)
    clients = []
    for origin in ("https://graph.example:9443", "https://lan.example:18443"):
        app = FastAPI()
        app.mount(
            "/service/v1",
            CanonicalServiceRoute(canonical, origin=origin, authority_receiver=inbox.receiver),
        )
        clients.append(TestClient(app, base_url=origin))
    request = action(public).model_dump(mode="json", by_alias=True)
    challenge = (
        clients[0]
        .post("/service/v1/devgraph/access-requests/v2/challenges", json={"request": request})
        .json()
    )
    assert challenge["receiver"] == inbox.receiver
    approval = dict(challenge=challenge, proof=proof, signature=signed(holder, challenge))
    first = clients[0].post("/service/v1/devgraph/access-requests/v2/actions", json=approval)
    second = clients[1].post("/service/v1/devgraph/access-requests/v2/actions", json=approval)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert inbox.pending()["total"] == 1
