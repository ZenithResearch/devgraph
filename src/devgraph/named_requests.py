"""Choose an explicit versioned request domain without broadening Work kinds."""

from devgraph.arena_requests import ARENA_REQUEST_SCHEMA, ArenaRequest, InvalidArenaRequest
from devgraph.auth.secs_issue_create import SecSIssueCreateDenied, _strict_json_object
from devgraph.work_requests import WORK_REQUEST_SCHEMA, InvalidWorkRequest, WorkRequest


def parse_named_request(raw: bytes) -> WorkRequest | ArenaRequest:
    try:
        value = _strict_json_object(raw, maximum_bytes=131_072, reason="invalid_named_request")
        parser = {WORK_REQUEST_SCHEMA: WorkRequest, ARENA_REQUEST_SCHEMA: ArenaRequest}.get(
            value.get("schema")
        )
        if parser is None:
            raise ValueError("unknown named request domain")
        return parser.from_json(raw)
    except (SecSIssueCreateDenied, InvalidArenaRequest, InvalidWorkRequest, TypeError, ValueError):
        raise InvalidWorkRequest("invalid_named_request") from None
