"""Owner-credential Arena reads through the fixed loopback API."""

import httpx

from devgraph.client.http import DevgraphHttpClient, DevgraphRequestContext
from devgraph.local_host import DEFAULT_CONFIG_PATH, load_local_config


def query_arena_snapshot(*, operation, arena_id=None, kind=None, work_id=None,
                         include_archived=False, after_id=None, after_resource=None,
                         limit=50, config_path=DEFAULT_CONFIG_PATH, transport=None):
    from devgraph.cli import DEFAULT_BASE_URL, CliError, read_local_read_credential

    if operation not in ("arena", "arena-members", "arena-of"):
        raise CliError("unsupported Arena read")
    if arena_id is not None and operation == "arena" and (
        include_archived or after_id is not None or limit != 50
    ):
        raise CliError("Arena list options require a list operation")
    config = load_local_config(config_path)
    assert config is not None
    credential = read_local_read_credential(config)
    owns_transport = transport is None
    active = transport or httpx.Client(trust_env=False, follow_redirects=False)
    try:
        client = DevgraphHttpClient(transport=active, base_url=DEFAULT_BASE_URL, timeout=10)
        context = DevgraphRequestContext(credential=credential)
        if operation == "arena-members":
            return client.get_arena_members(context, arena_id=arena_id,
                after_resource=after_resource, limit=limit).model_dump(mode="json")
        if operation == "arena-of":
            return client.get_work_arena(context, kind=kind, work_id=work_id).model_dump(
                mode="json")
        if arena_id is not None:
            return {"item": client.get_arena(context, arena_id=arena_id).model_dump(mode="json")}
        return client.list_arenas(context, include_archived=include_archived,
            after_id=after_id, limit=limit).model_dump(mode="json")
    except ValueError:
        raise CliError("invalid Arena read") from None
    finally:
        if owns_transport:
            active.close()
