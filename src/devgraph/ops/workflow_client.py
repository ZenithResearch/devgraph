"""Bounded workflow reads through the configured local read credential."""

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from devgraph.client.http import DevgraphHttpClient, DevgraphRequestContext
from devgraph.kanban import BoardFilter
from devgraph.local_host import DEFAULT_CONFIG_PATH, load_local_config
from devgraph.model.validation import validate_work_object_id
from devgraph.workflow_contract import WORK_KINDS


class WorkflowRead(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_name: Literal[
        "devgraph.kanban.v1", "devgraph.workflows.v1", "devgraph.work-workflow.v1"
    ] = Field(alias="schema")


def query_workflow_snapshot(
    *, operation, query="", kind=None, work_id=None, config_path=DEFAULT_CONFIG_PATH, transport=None
):
    from devgraph.cli import DEFAULT_BASE_URL, CliError, read_local_read_credential

    if operation == "board":
        BoardFilter.parse(query)
        path = "/monitor/kanban/v1" + ("?" + query if query else "")
    elif operation == "workflows":
        path = "/workflows/v1"
    elif operation == "workflow" and kind in WORK_KINDS:
        validate_work_object_id(work_id)
        path = f"/work/{kind}/{work_id}/workflow"
    else:
        raise CliError("unsupported workflow read")
    config = load_local_config(config_path)
    assert config is not None
    credential = read_local_read_credential(config)
    active = transport or httpx.Client(trust_env=False, follow_redirects=False)
    try:
        client = DevgraphHttpClient(transport=active, base_url=DEFAULT_BASE_URL, timeout=10)
        return client._request(
            WorkflowRead, "GET", path, context=DevgraphRequestContext(credential=credential)
        ).model_dump(mode="json", by_alias=True)
    finally:
        if transport is None:
            active.close()
