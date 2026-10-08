import re

import httpx
from fastapi.testclient import TestClient
from scripts import kanban_live_preview as preview

TICKET = "a" * 64


def fixture():
    rows = []
    for kind, ids in (
        ("Initiative", ["root"]),
        ("Project", ["project"]),
        ("Issue", ["issue"]),
        ("Task", [f"t{i:03}" for i in range(110)]),
    ):
        rows.extend(
            dict(
                id=key,
                kind=kind,
                title=key,
                description="Actual API description",
                status="accepted" if key == "t000" else "draft",
                priority=0,
                version=1,
                artifact_ids=[],
                external_link_ids=[],
            )
            for key in ids
        )
    edges = {
        ("HAS_CHILD", "Initiative", "Project"): [["root", "project"]],
        ("HAS_CHILD", "Project", "Issue"): [["project", "issue"]],
        ("HAS_CHILD", "Issue", "Task"): [["issue", f"t{i:03}"] for i in range(105)],
        ("BLOCKS", "Task", "Task"): [["t000", f"t{i:03}"] for i in range(1, 104)]
        + [["t105", "t106"]],
    }
    calls, state = [], {"version": 26, "valid": True}

    def handle(request):
        calls.append(request)
        assert request.url.host == "127.0.0.1" and request.url.port == 8080
        if request.headers.get("authorization") != "Bearer real-reader" or not state["valid"]:
            return httpx.Response(401)
        path = request.url.path
        if path.startswith("/monitor/todos/v1") and state.get("todos"):
            return httpx.Response(200, json=state["todos"])
        if path == "/ready":
            return httpx.Response(
                200, json={"ready": True, "current_applied_version": state["version"]}
            )
        if path == "/monitor/snapshot":
            return httpx.Response(
                200,
                json={
                    "graph_nodes": [
                        {
                            **row,
                            "key": row["kind"] + ":" + row["id"],
                            "category": "work",
                            "archived": False,
                        }
                        for row in rows
                    ],
                    "graph_edges": [],
                    "total_work": len(rows),
                },
            )
        if path == "/initiative-observations" or "/relationships/" in path:
            return httpx.Response(200, json={"items": []})
        if path == "/query/cypher":
            import json

            body = json.loads(request.content)
            match = re.search(r"a:(\w+)\)-\[:(\w+)\]->\(b:(\w+)\)", body["query"])
            a, rel, b = match.groups()
            found = edges[(rel, a, b)]
            params = body["parameters"]
            if "target" in params:
                found = [r for r in found if r[0] == params["source"] and r[1] > params["target"]]
            elif "source" in params:
                found = [r for r in found if r[0] > params["source"]]
            return httpx.Response(200, json={"rows": found[:100]})
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "work":
            items = [
                r
                for r in rows
                if r["kind"] == parts[1] and r["id"] > request.url.params.get("after_id", "")
            ]
            return httpx.Response(200, json={"items": items[: int(request.url.params["limit"])]})
        if len(parts) == 3 and parts[0] == "work":
            return httpx.Response(
                200, json=next(r for r in rows if (r["kind"], r["id"]) == tuple(parts[1:]))
            )
        if path.endswith("supporting-material"):
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        return httpx.Response(404)

    client = TestClient(
        preview.create_preview(
            TICKET, lambda: "real-reader", transport=httpx.MockTransport(handle)
        ),
        base_url=preview.ORIGIN,
    )
    return client, calls, state


def test_todo_preview_preserves_new_envelopes_and_missing_capability():
    client, calls, state = fixture()
    auth = {"Authorization": "Bearer real-reader"}
    assert client.get("/monitor/todos/v1").status_code == 401
    assert not calls
    missing = client.get("/monitor/todos/v1?limit=6", headers=auth)
    assert missing.status_code == 501
    assert "does not support base Todo" in missing.text
    state["todos"] = {"schema": "devgraph.todos.v1", "items": [], "counts": {"total": 0}}
    response = client.get("/monitor/todos/v1?limit=6&status=review", headers=auth)
    assert response.status_code == 200 and response.json() == state["todos"]
    assert calls[-1].url.params["status"] == "review"
    state["todos"] = {"kind": "Todo", "id": "daily", "description": "Read context"}
    assert client.get("/monitor/todos/v1/daily", headers=auth).json() == state["todos"]
    assert client.get("/monitor/todos/v1/bad%20id", headers=auth).status_code == 400
    state["valid"] = False
    assert client.get("/monitor/todos/v1", headers=auth).status_code == 401


def test_live_board_full_counts_pagination_scope_and_legacy_stages():
    client, calls, _ = fixture()
    auth = {"Authorization": "Bearer real-reader"}
    board = client.get("/monitor/kanban/v1", headers=auth).json()
    assert board["total"] == 113
    assert len(board["columns"][0]["items"]) == 30
    assert all(c["count"] == 0 for c in board["columns"][1:])
    assert all(c["stage"] is None for c in board["columns"][0]["items"])
    scope = client.get("/monitor/kanban/v1?scope=Project/project", headers=auth).json()
    assert scope["total"] == 1
    descendants = client.get(
        "/monitor/kanban/v1?scope=Project/project&descendants=true", headers=auth
    ).json()
    assert descendants["total"] == 106  # Does not include five standalone Tasks.
    first = board["columns"][0]
    second = client.get(
        "/monitor/kanban/v1",
        params={"column": "backlog", "after": first["next_cursor"], "revision": board["revision"]},
        headers=auth,
    ).json()
    assert not {c["key"] for c in first["items"]} & {
        c["key"] for c in second["columns"][0]["items"]
    }
    work = client.get("/work/Task/t000", headers=auth).json()
    assert work["status"] == "accepted"
    detail = client.get("/work/Task/t000/workflow", headers=auth).json()
    assert detail["workflow"] is None and detail["transitions"] == []
    assert detail["completion_gaps"] == ["Stage not set"]
    assert client.get("/work/Task/t000/supporting-material", headers=auth).status_code == 200
    assert any(r.url.params.get("after_id") == "t099" for r in calls)
    assert all(r.method == "GET" or r.url.path == "/query/cypher" for r in calls)


def test_legacy_preview_never_classifies_unparented_draft_tasks():
    client, _, _ = fixture()
    response = client.get(
        "/monitor/kanban/v1",
        headers={"Authorization": "Bearer real-reader"},
        params={
            "kind": "Task",
            "parentage": "standalone",
            "queue": "not_started",
            "column": "backlog",
            "order": "priority",
            "limit": 2,
        },
    )
    assert response.status_code == 200
    board = response.json()
    assert board["total"] == 0
    cards = board["columns"][0]["items"]
    assert cards == []
    assert all(
        c["stage"] is None and c["lifecycle"] == "draft" and c["parent"] is None for c in cards
    )


def test_real_credentials_session_denials_revocation_and_closed_write_boundary():
    client, calls, state = fixture()
    page = client.get("/monitor/kanban/")
    assert 'name="devgraph-local-reader"' in page.text
    assert "real-reader" not in page.text and "Live local data" in page.text
    assert client.get("/monitor/kanban/v1").status_code == 401
    assert not calls
    assert (
        client.get(
            "/monitor/kanban/v1", headers={"Authorization": "Bearer fake-credential-monitor"}
        ).status_code
        == 401
    )
    auth = {"Authorization": "Bearer " + TICKET}
    assert (
        client.post(
            "/preview/session", headers={**auth, "Origin": "https://untrusted.invalid"}
        ).status_code
        == 403
    )
    response = client.post("/preview/session", headers=auth)
    assert response.status_code == 204
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert client.post("/preview/session", headers=auth).status_code == 401
    assert client.get("/monitor/kanban/v1").status_code == 200
    for path in ("/work/Task", "/work-operations/v1", "/arena-operations/v1", "/query/cypher"):
        assert client.post(path, json={}).status_code == 405
    assert (
        client.get("/monitor/kanban/v1", headers={"Host": "untrusted.invalid"}).status_code == 403
    )
    assert (
        client.get("/monitor/kanban/v1", headers={"Sec-Fetch-Site": "cross-site"}).status_code
        == 403
    )
    state["valid"] = False
    assert client.get("/monitor/kanban/v1").status_code == 401  # Cached data is protected too.


def test_new_schema_is_never_misrepresented_as_unclassified():
    client, _, state = fixture()
    state["version"] = 27
    assert (
        client.get(
            "/monitor/kanban/v1", headers={"Authorization": "Bearer real-reader"}
        ).status_code
        == 503
    )


def test_unified_monitor_uses_current_assets_authenticated_cache_and_same_origin_navigation():
    client, calls, state = fixture()
    assert client.get("/").url.path == "/monitor"
    monitor = client.get("/monitor")
    assert monitor.status_code == 200 and "Live local data" in monitor.text
    assert "schedule(); refresh();" in monitor.text
    assert "real-reader" not in monitor.text
    assert "http://127.0.0.1:8080" not in client.get("/monitor/kanban/").text
    assert 'content="local-session"' in client.get("/monitor/selection").text
    assert "http://127.0.0.1:8080" not in client.get("/monitor/selection-assets/page/app.mjs").text
    for path in ("/monitor/snapshot", "/monitor/topology/v1", "/initiative-observations"):
        assert client.get(path).status_code == 401
    assert not calls
    assert (
        client.post("/preview/session", headers={"Authorization": "Bearer " + TICKET}).status_code
        == 204
    )
    assert client.get("/monitor/snapshot").json()["total_work"] == 113
    topology = client.get("/monitor/topology/v1?work_kind=Task&node_limit=5").json()
    assert topology["counts"]["matching_nodes"] == 110
    assert len(topology["graph_nodes"]) == 5 and not topology["complete"]
    assert sum(r.url.path == "/monitor/snapshot" for r in calls) == 1
    assert client.get("/monitor/topology/v1?bad=filter").status_code == 400
    assert client.get("/work/Issue/issue/relationships/children").status_code == 200
    assert client.get("/initiative-observations").status_code == 200
    assert client.get("/work/Task/t000/arbitrary").status_code == 404
    state["valid"] = False
    for path in ("/monitor/snapshot", "/monitor/topology/v1", "/initiative-observations"):
        assert client.get(path).status_code == 401
