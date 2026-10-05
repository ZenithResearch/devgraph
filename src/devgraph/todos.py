"""Read-only base Todo projection. It never broadens the five public Work kinds."""

import re
from dataclasses import replace
from urllib.parse import parse_qsl

from devgraph.model.base import utc_now
from devgraph.model.validation import validate_canonical_work_object_properties
from devgraph.policy.redaction import redact_text
from devgraph.storage.base import StorageUnavailable
from devgraph.storage.todos import TodoQuery, exact_todo


class InvalidTodoFilter(ValueError):
    pass


def parse_todo_query(query: str) -> TodoQuery:
    if query == "":
        return TodoQuery()
    try:
        if len(query) > 4096 or re.search(r"%(?![0-9a-fA-F]{2})", query):
            raise ValueError
        pairs = parse_qsl(query, keep_blank_values=True, errors="strict",
                          strict_parsing=True, max_num_fields=6)
        values = dict(pairs)
        if len(values) != len(pairs) or set(values) - TodoQuery.__dataclass_fields__.keys():
            raise ValueError
        if "limit" in values:
            if not re.fullmatch(r"[0-9]{1,3}", values["limit"]):
                raise ValueError
            values["limit"] = int(values["limit"])
        result = TodoQuery(**values)
        return replace(result, q=result.q.strip())
    except (ValueError, TypeError):
        raise InvalidTodoFilter("invalid_todo_filter") from None


def project_todo(node, *, description=False):
    if not exact_todo(node):
        raise StorageUnavailable("invalid Todo identity")
    try:
        data = validate_canonical_work_object_properties(
            "Todo", node.id, node.properties, archived=node.archived, allow_unknown=True
        )
    except (ValueError, TypeError):
        raise StorageUnavailable("malformed Todo record") from None
    result = {
        "kind": "Todo", "id": node.id, "title": redact_text(data["title"]),
        "status": data["status"], "priority": str(data["priority"]),
        "version": str(data["version"]), "created_at": data["created_at"],
        "updated_at": data["updated_at"], "archived": node.archived,
    }
    if description:
        result["description"] = redact_text(data["description"])
    return result


def build_todos(storage, query: TodoQuery):
    page = storage.todo_page(query)
    nodes = page.nodes[:query.limit]
    more = len(page.nodes) > query.limit
    return {
        "schema": "devgraph.todos.v1", "generated_at": utc_now().isoformat(),
        "items": [project_todo(node) for node in nodes],
        "next_after_id": nodes[-1].id if more else None, "has_more": more,
        "counts": page.counts, "matching_count": page.matching_count,
    }


def read_todo(storage, todo_id):
    node = storage.todo_detail(todo_id)
    if node is None:
        raise KeyError("Todo not found")
    return project_todo(node, description=True)
