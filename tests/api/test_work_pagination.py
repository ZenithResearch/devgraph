"""Public client/API paging compatibility and complete multi-page reads."""

from __future__ import annotations

import pytest
from test_app_scaffold import FAKE_CREDENTIAL
from test_routes_read_create import _client

from devgraph.auth.scopes import SCOPE_READ
from devgraph.client import DevgraphHttpClient, DevgraphRequestContext
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Initiative, Issue, Project, Proposal, Task


@pytest.mark.parametrize("model", [Proposal, Initiative, Project, Issue, Task])
@pytest.mark.parametrize("descending", [False, True])
def test_client_pages_all_work_without_changing_envelope(model, descending) -> None:
    transport, services = _client(frozenset({SCOPE_READ}))
    repository = WorkObjectRepository(services.storage)
    for index in range(107):
        repository.create(model(id=f"item-{index:03}", title="Pagination fixture"))
    repository.archive(model.__name__, "item-051")
    client = DevgraphHttpClient(transport=transport, base_url="http://testserver", timeout=2)
    context = DevgraphRequestContext(credential=FAKE_CREDENTIAL)
    found = []
    after_id = None
    for _ in range(5):
        page = client.list_work(
            context, kind=model.__name__, limit=40, after_id=after_id, descending=descending
        )
        found.extend(item.id for item in page.items)
        if len(page.items) < 40:
            break
        after_id = page.items[-1].id
    expected = [f"item-{i:03}" for i in range(107) if i != 51]
    assert found == sorted(expected, reverse=descending)
    assert len(set(found)) == 106
    headers = {"Authorization": f"Bearer {FAKE_CREDENTIAL}"}
    response = transport.get(f"/work/{model.__name__}", headers=headers)
    assert set(response.json()) == {"items"}  # Older strict clients still decode it.
    assert len(response.json()["items"]) == 50
    with_archive = client.list_work(
        context, kind=model.__name__, after_id="item-050", limit=1, include_archived=True
    )
    assert with_archive.items[0].id == "item-051"


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "after_id=bad_id", "limit=x"])
def test_invalid_paging_never_queries_or_audits(query) -> None:
    client, services = _client(frozenset({SCOPE_READ}))
    response = client.get(
        f"/work/Issue?{query}", headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"}
    )
    assert response.status_code in (400, 422)
    assert services.authorized_graph._audit_log.records == []


def test_pagination_does_not_widen_read_authority() -> None:
    client, services = _client(frozenset())
    response = client.get(
        "/work/Issue?limit=100&descending=true",
        headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"},
    )
    assert response.status_code == 403
    assert services.authorized_graph._audit_log.records == []
