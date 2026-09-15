"""Reject malformed or miscorrelated Arena success responses."""

from copy import deepcopy

import pytest
from tests.client.test_http_client import RecordingTransport, Response

from devgraph.client import (
    DevgraphHttpClient,
    DevgraphInvalidSuccessEnvelope,
    DevgraphRequestContext,
)

ARENA = dict(schema_version=1, kind="Arena", id="gallery", title="Gallery", description="",
             version=1, archived=False, created_at="2026-09-14T00:00:00+00:00",
             updated_at="2026-09-14T00:00:00+00:00")
READ = DevgraphRequestContext(credential="synthetic-arena-read-only")


def client(body):
    return DevgraphHttpClient(base_url="http://testserver", timeout=5,
                             transport=RecordingTransport([Response(200, body)]))


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 1.0), ("kind", "Task"), ("id", "elsewhere"),
    ("version", True), ("version", 0), ("archived", "false"), ("created_at", "2026-09-14"),
    ("status", "draft"), ("title", ""),
])
def test_arena_record_is_strict_and_correlated(field, value):
    body = {**ARENA, field: value}
    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client(body).get_arena(READ, arena_id="gallery")


def test_arena_paging_rejects_duplicates_wrong_archive_filter_and_cursor():
    for items, query in [
        ([ARENA, ARENA], {}),
        ([{**ARENA, "archived": True}], {}),
        ([ARENA], {"after_id": "gallery"}),
        ([ARENA, {**ARENA, "id": "later"}], {"limit": 1}),
    ]:
        with pytest.raises(DevgraphInvalidSuccessEnvelope):
            client({"items": deepcopy(items)}).list_arenas(READ, **query)


def test_effective_membership_rejects_impossible_root_and_inherited_flags():
    for body in [
        {"arena": ARENA, "root_kind": "Issue", "root_id": "issue", "inherited": True},
        {"arena": ARENA, "root_kind": "Initiative", "root_id": "root", "inherited": False},
        {"arena": None, "root_kind": "Task", "root_id": "task", "inherited": True},
    ]:
        with pytest.raises(DevgraphInvalidSuccessEnvelope):
            client(body).get_work_arena(READ, kind="Task", work_id="task")
