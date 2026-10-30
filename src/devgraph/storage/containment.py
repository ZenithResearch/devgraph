"""Validation shared by the two storage adapters for scoped containment reads."""

from devgraph.model.validation import (
    CANONICAL_WORK_KINDS,
    validate_page_limit,
    validate_work_object_id,
)


def validate_containment_subject(label: str, node_id: str) -> None:
    if label not in CANONICAL_WORK_KINDS:
        raise ValueError("invalid containment subject")
    validate_work_object_id(node_id)


def validate_containment_keys(keys: tuple[tuple[str, str], ...]) -> None:
    if not isinstance(keys, tuple) or not 1 <= len(keys) <= 100:
        raise ValueError("invalid containment batch")
    for key in keys:
        if not isinstance(key, tuple) or len(key) != 2:
            raise ValueError("invalid containment subject")
        validate_containment_subject(*key)
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate containment subject")


def validate_arena_page(arena_id: str, after_resource: str | None, limit: int) -> None:
    validate_work_object_id(arena_id)
    validate_page_limit(limit)
    if after_resource is not None:
        if not isinstance(after_resource, str) or after_resource.count("/") != 1:
            raise ValueError("invalid Arena member cursor")
        kind, work_id = after_resource.split("/")
        if kind not in {"Initiative", "Task"}:
            raise ValueError("invalid Arena member cursor")
        validate_work_object_id(work_id)
