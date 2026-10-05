"""Exact-base Todo read contract, separate from public mutable Work kinds."""

from dataclasses import dataclass
from heapq import nsmallest

from devgraph.model.validation import validate_page_limit, validate_work_object_id
from devgraph.policy.redaction import redact_text
from devgraph.storage.base import NodeRecord, StorageUnavailable

SEARCH_SOURCE_LIMIT = 10_000

STATUSES = ("draft", "review", "accepted", "archived")


@dataclass(frozen=True)
class TodoQuery:
    limit: int = 50
    after_id: str | None = None
    status: str | None = None
    archived: str = "exclude"
    q: str = ""
    queue: str = ""
    order: str = "id"
    after_priority: str | None = None

    def __post_init__(self):
        validate_page_limit(self.limit)
        if self.after_id is not None:
            validate_work_object_id(self.after_id)
        if self.status is not None and self.status not in STATUSES:
            raise ValueError("invalid_todo_status")
        if self.archived not in ("include", "exclude", "only"):
            raise ValueError("invalid_todo_archive_filter")
        if self.queue not in ("", "not_started") or self.order not in ("id", "priority"):
            raise ValueError("invalid_todo_order_or_queue")
        if self.order == "priority" and bool(self.after_id) != (self.after_priority is not None):
            raise ValueError("incomplete_todo_priority_cursor")
        if self.after_priority is not None:
            import re
            if (self.order != "priority" or not isinstance(self.after_priority, str)
                    or not re.fullmatch(r"-?(0|[1-9][0-9]{0,18})", self.after_priority)
                    or not -(2**63) <= int(self.after_priority) < 2**63):
                raise ValueError("invalid_todo_priority_cursor")
        if not isinstance(self.q, str) or len(self.q) > 200 or any(
            ord(character) < 32 or ord(character) == 127 for character in self.q
        ):
            raise ValueError("invalid_todo_search")


@dataclass(frozen=True)
class TodoPage:
    nodes: tuple[NodeRecord, ...]  # At most limit + one look-ahead item.
    counts: dict[str, int]
    matching_count: int
    remaining_count: int


def exact_todo(node: NodeRecord) -> bool:
    return node.label == "Todo" and node.properties.get("kind") == "Todo"


def not_started(node):
    from devgraph.workflow_contract import decode_state

    state = node.properties.get("workflow_json")
    try:
        return not node.archived and (
            decode_state(state).stage == "backlog"
            if state else node.properties.get("status") == "draft"
        )
    except (ValueError, TypeError):
        raise StorageUnavailable("invalid Todo workflow") from None


def order_key(node, query):
    if query.order == "id":
        return (node.id,)
    priority = node.properties.get("priority")
    if type(priority) is not int or not -(2**63) <= priority < 2**63:
        raise StorageUnavailable("invalid Todo priority")
    return (-priority, node.id)


def after_cursor(node, query):
    if query.after_id is None:
        return True
    cursor = ((-int(query.after_priority), query.after_id)
              if query.order == "priority" else (query.after_id,))
    return order_key(node, query) > cursor


def validate_todo_page(page: TodoPage, query: TodoQuery) -> TodoPage:
    expected = {"total", *STATUSES}
    if set(page.counts) != expected or any(
        type(value) is not int or value < 0 for value in page.counts.values()
    ) or sum(page.counts[key] for key in STATUSES) != page.counts["total"]:
        raise StorageUnavailable("invalid Todo counts")
    if type(page.matching_count) is not int or page.matching_count < 0:
        raise StorageUnavailable("invalid Todo matching count")
    expected_count = page.counts[query.status] if query.status else page.counts["total"]
    if page.matching_count != expected_count:
        raise StorageUnavailable("invalid Todo matching count")
    if (type(page.remaining_count) is not int or not 0 <= page.remaining_count <= expected_count
            or (query.after_id is None and page.remaining_count != expected_count)
            or len(page.nodes) != min(query.limit + 1, page.remaining_count)):
        raise StorageUnavailable("Todo page changed during read; retry")
    keys = [order_key(node, query) for node in page.nodes]
    if keys != sorted(set(keys)) or any(
        not exact_todo(node) or not after_cursor(node, query)
        or (query.queue == "not_started" and not not_started(node))
        or (query.status is not None and node.properties.get("status") != query.status)
        or (query.archived == "exclude" and node.archived)
        or (query.archived == "only" and not node.archived)
        for node in page.nodes
    ):
        raise StorageUnavailable("invalid Todo identity or ordering")
    page_counts = dict.fromkeys(STATUSES, 0)
    for node in page.nodes:
        status = node.properties.get("status")
        if status not in STATUSES:
            raise StorageUnavailable("invalid Todo page status")
        page_counts[status] += 1
    if any(page_counts[status] > page.counts[status] for status in STATUSES):
        raise StorageUnavailable("Todo counts changed during read; retry")
    return page


def select_todo_page(records, query: TodoQuery) -> TodoPage:
    """Bounded search over safe display titles; never probe redacted text via counts."""
    counts = dict.fromkeys(("total", *STATUSES), 0)
    matching_count = 0
    remaining_count = 0
    scanned = 0
    seen = set()
    query_text = query.q.lower()

    def candidates():
        nonlocal matching_count, remaining_count, scanned
        for node in records:
            if not exact_todo(node):
                continue
            if query.archived == "exclude" and node.archived:
                continue
            if query.archived == "only" and not node.archived:
                continue
            scanned += 1
            bounded = query.q or query.queue or query.order == "priority"
            if bounded and scanned > SEARCH_SOURCE_LIMIT:
                raise StorageUnavailable("Todo search capacity exceeded")
            if node.id in seen:
                raise StorageUnavailable("ambiguous Todo identity")
            seen.add(node.id)
            if query.queue == "not_started" and not not_started(node):
                continue
            title = node.properties.get("title")
            if not isinstance(title, str):
                raise StorageUnavailable("malformed Todo title")
            if query_text not in redact_text(title).lower() and query_text not in node.id.lower():
                continue
            status = node.properties.get("status")
            if status not in STATUSES:
                raise StorageUnavailable("malformed Todo status")
            counts["total"] += 1
            counts[status] += 1
            if query.status is not None and status != query.status:
                continue
            matching_count += 1
            if after_cursor(node, query):
                remaining_count += 1
                yield node

    nodes = tuple(nsmallest(query.limit + 1, candidates(), key=lambda node: order_key(node, query)))
    return validate_todo_page(TodoPage(nodes, counts, matching_count, remaining_count), query)
