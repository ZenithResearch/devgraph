import pytest
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.model.work import Initiative, Project, Task


@pytest.mark.parametrize(
    "relation,edge,incoming",
    [
        ("children", "HAS_CHILD", False),
        ("dependencies", "DEPENDS_ON", False),
        ("dependents", "DEPENDS_ON", True),
        ("blockers", "BLOCKS", True),
        ("blocked", "BLOCKS", False),
    ],
)
def test_bounded_relationship_pages_in_both_directions(relation, edge, incoming):
    services = build_services(frozenset({"devgraph.read"}))
    subject = (
        Initiative(id="root", title="root")
        if relation == "children"
        else Task(id="root", title="root")
    )
    services.storage.create_node(subject.kind, subject.id, subject.to_node_properties())
    for index in range(107):
        cls = Project if relation == "children" else Task
        work = cls(id=f"work-{index:03}", title="item")
        services.storage.create_node(work.kind, work.id, work.to_node_properties())
        source, target = (work, subject) if incoming else (subject, work)
        services.storage.create_edge(source.kind, source.id, edge, target.kind, target.id)
    client = TestClient(create_app(services))
    url = f"/work/{subject.kind}/root/relationships/{relation}"
    assert client.get(url).status_code == 401
    assert (
        client.get(
            url, headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"}, params={"limit": 101}
        ).status_code
        == 422
    )
    cursor = None
    seen = []
    while True:
        params = {"limit": 40}
        if cursor:
            params["after_resource"] = cursor
        response = client.get(
            url, headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"}, params=params
        )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) <= 40
        if not items:
            break
        seen.extend(item["id"] for item in items)
        cursor = items[-1]["kind"] + "/" + items[-1]["id"]
    assert seen == [f"work-{index:03}" for index in range(107)]
