"""Exercise the frontend's generated pagination URL against the real local API."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.model.work import Initiative, Project


def test_frontend_relationship_cursor_is_accepted_by_work_api():
    node = os.environ.get("DEVGRAPH_NODE_BINARY") or shutil.which("node")
    if not node or subprocess.run([node, "--version"], capture_output=True, check=False).returncode:
        pytest.skip("Set DEVGRAPH_NODE_BINARY to a working Node runtime for the wire check")

    services = build_services(frozenset({"devgraph.read"}))
    parent = Initiative(id="parent", title="Parent")
    services.storage.create_node(parent.kind, parent.id, parent.to_node_properties())
    for index in range(51):
        child = Project(id=f"child-{index:02}", title=f"Child {index}")
        services.storage.create_node(child.kind, child.id, child.to_node_properties())
        services.storage.create_edge(parent.kind, parent.id, "HAS_CHILD", child.kind, child.id)
    client = TestClient(create_app(services))
    headers = {"Authorization": f"Bearer {FAKE_CREDENTIAL}"}
    page = client.get("/work/Initiative/parent/relationships/children?limit=50", headers=headers)
    assert page.status_code == 200
    assert len(page.json()["items"]) == 50

    script = r"""
import { readFileSync } from 'node:fs';
import { contextWithFunctions } from './monitor_test_helpers.mjs';
const items = JSON.parse(readFileSync(0, 'utf8'));
let path;
const c = contextWithFunctions(['readPath', 'loadRelationship'], {
  AbortController,
  state: { authEpoch: 0 },
  detailState: {
    node: { category: 'work', kind: 'Initiative', id: 'parent' },
    serial: 1, resourcesSerial: 1,
    relations: new Map([['children', { items, loaded: true, more: true, loading: false }]]),
  },
  renderGraphSelection() {},
  getJson: async value => { path = value; return { items: [] }; },
});
await c.loadRelationship('children', true);
process.stdout.write(JSON.stringify(path));
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=Path(__file__).parent,
        input=json.dumps(page.json()["items"]),
        capture_output=True,
        text=True,
        check=True,
    )
    next_url = json.loads(result.stdout)
    next_page = client.get(next_url, headers=headers)
    assert next_page.status_code == 200, next_page.text
    assert [item["id"] for item in next_page.json()["items"]] == ["child-50"]
