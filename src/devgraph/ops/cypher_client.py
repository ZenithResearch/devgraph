"""Private request file → existing read credential → fixed loopback HTTP API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from devgraph.client.http import DevgraphHttpClient, DevgraphRequestContext
from devgraph.cypher_read import MAX_REQUEST_BYTES, CypherReadError, parse_cypher_request
from devgraph.local_host import (
    DEFAULT_CONFIG_PATH,
    load_local_config,
    read_local_read_credential,
)
from devgraph.ops.secs_issue_create_receiver import (
    LocalSecSIssueCreateError,
    _read_private_bounded_file,
)


def query_cypher_snapshot(
    *, request_file: Path, config_path: Path = DEFAULT_CONFIG_PATH, transport=None,
) -> dict[str, Any]:
    try:
        raw = _read_private_bounded_file(
            request_file, label="Cypher read request", maximum_bytes=MAX_REQUEST_BYTES,
        )
    except LocalSecSIssueCreateError:
        raise CypherReadError("cypher_request_file_unavailable") from None
    compiled = parse_cypher_request(raw)
    config = load_local_config(config_path)
    assert config is not None
    credential = read_local_read_credential(config)
    owns_transport = transport is None
    active_transport = transport or httpx.Client(trust_env=False, follow_redirects=False)
    try:
        return DevgraphHttpClient(
            transport=active_transport, base_url="http://127.0.0.1:8080", timeout=15.0,
        ).query_cypher(
            DevgraphRequestContext(credential=credential),
            request_json=compiled.canonical_request,
        )
    finally:
        if owns_transport:
            active_transport.close()
