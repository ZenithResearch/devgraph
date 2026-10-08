"""Validation helpers for storage-level delegated Work filtering."""

from __future__ import annotations

from dataclasses import dataclass

from devgraph.model.validation import (
    CANONICAL_WORK_KINDS,
    validate_page_limit,
    validate_work_object_id,
)


def validate_selectors(
    work_ids: tuple[str, ...], arena_ids: tuple[str, ...]
) -> tuple[set[tuple[str, str]], set[str]]:
    if not isinstance(work_ids, tuple) or not isinstance(arena_ids, tuple):
        raise TypeError("delegated selectors must be tuples")
    exact: set[tuple[str, str]] = set()
    for resource in work_ids:
        if not isinstance(resource, str) or resource.count("/") != 1:
            raise ValueError("invalid delegated Work selector")
        kind, work_id = resource.split("/")
        if kind not in CANONICAL_WORK_KINDS:
            raise ValueError("invalid delegated Work selector")
        validate_work_object_id(work_id)
        exact.add((kind, work_id))
    arenas: set[str] = set()
    for arena_id in arena_ids:
        validate_work_object_id(arena_id)
        arenas.add(arena_id)
    if len(exact) != len(work_ids) or len(arenas) != len(arena_ids):
        raise ValueError("duplicate delegated selector")
    return exact, arenas


@dataclass(frozen=True)
class DelegatedWorkSelector:
    """One conjunctive selector group; groups are combined disjunctively."""

    work_ids: tuple[str, ...] = ()
    arena_ids: tuple[str, ...] = ()
    work_kinds: tuple[str, ...] = (
        "Proposal",
        "Initiative",
        "Project",
        "Issue",
        "Task",
    )
    include_archived: bool = False

    def __post_init__(self) -> None:
        validate_selectors(self.work_ids, self.arena_ids)
        if (
            not self.work_kinds
            or any(kind not in CANONICAL_WORK_KINDS for kind in self.work_kinds)
            or len(set(self.work_kinds)) != len(self.work_kinds)
            or type(self.include_archived) is not bool
        ):
            raise ValueError("invalid delegated Work selector")

    def as_parameters(self) -> dict[str, object]:
        return {
            "work_ids": list(self.work_ids),
            "arena_ids": list(self.arena_ids),
            "work_kinds": list(self.work_kinds),
            "include_archived": self.include_archived,
        }


def validate_work_filter(
    *,
    label: str,
    selectors: tuple[DelegatedWorkSelector, ...],
    work_kinds: tuple[str, ...],
    include_archived: bool,
    after_id: str | None,
    limit: int,
) -> None:
    if label not in CANONICAL_WORK_KINDS:
        raise ValueError("invalid delegated Work kind")
    if (
        not isinstance(selectors, tuple)
        or not selectors
        or any(not isinstance(selector, DelegatedWorkSelector) for selector in selectors)
    ):
        raise ValueError("invalid delegated Work selectors")
    if (
        not isinstance(work_kinds, tuple)
        or not work_kinds
        or any(kind not in CANONICAL_WORK_KINDS for kind in work_kinds)
        or len(set(work_kinds)) != len(work_kinds)
    ):
        raise ValueError("invalid delegated Work kinds")
    if type(include_archived) is not bool:
        raise TypeError("include_archived must be boolean")
    if after_id is not None:
        validate_work_object_id(after_id)
    validate_page_limit(limit)
