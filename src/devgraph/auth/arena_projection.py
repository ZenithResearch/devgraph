"""Bounded Arena metadata reads and intersection with independent Work grants."""

import re
from urllib.parse import parse_qs

from devgraph.arenas import ArenaRepository
from devgraph.auth.delegated_contract import ArenaReadGrant
from devgraph.auth.errors import ForbiddenError
from devgraph.model.repository import WorkObjectRepository
from devgraph.storage.delegated import DelegatedWorkSelector
from devgraph.todo_views import todo_summary

ID = r"[a-z0-9][a-z0-9-]{0,255}"


def arena_target(target):
    path, _, query = target.partition("?")
    if path == "/arenas":
        return "arena.list", path, query
    if re.fullmatch(r"/arenas/" + ID + r"(?:/members)?", path):
        if query and not path.endswith("/members"):
            raise ValueError("unsupported_read_target")
        return "arena.get", path, query
    return None


def project_arena_read(storage, grants, target):
    parsed = arena_target(target)
    if parsed is None:
        raise ValueError("unsupported_read_target")
    operation, path, query = parsed
    allowed = tuple(
        sorted(
            {
                identifier
                for g in grants
                if isinstance(g, ArenaReadGrant)
                and operation.removeprefix("arena.") in getattr(g, "operations", ("list", "get"))
                for identifier in g.arena_ids
            }
        )
    )
    if not allowed:
        raise ForbiddenError("scope_not_granted", correlation_id="arena-read")
    fields = parse_qs(query, strict_parsing=True, max_num_fields=3) if query else {}
    names = {"limit", "after_id"} if operation == "arena.list" else {"limit", "after_resource"}
    if set(fields) - names or any(len(v) != 1 for v in fields.values()):
        raise ValueError("invalid_read_target")
    limit = int(fields.get("limit", ["50"])[0])
    if not 1 <= limit <= 100:
        raise ValueError("invalid_read_target")
    with storage.work_mutation_transaction():
        if operation == "arena.list":
            after = fields.get("after_id", [None])[0]
            from devgraph.model.validation import validate_work_object_id

            if after is not None:
                validate_work_object_id(after)
            return {
                "items": [
                    ArenaRepository._from_node(n).model_dump(mode="json")
                    for n in storage.delegated_arena_page(allowed, after_id=after, limit=limit)
                ]
            }
        identifier = path.split("/")[2]
        if identifier not in allowed or storage.get_node("Arena", identifier) is None:
            raise KeyError("not_found")
        if not path.endswith("/members"):
            return ArenaRepository(storage).get(identifier).model_dump(mode="json")
        work = [g for g in grants if g.scope == "devgraph.work.read" and "list" in g.operations]
        if not work:
            raise ForbiddenError("scope_not_granted", correlation_id="arena-read")
        selectors = tuple(
            DelegatedWorkSelector(
                work_ids=g.work_ids,
                arena_ids=g.arena_ids,
                work_kinds=g.work_kinds,
                include_archived=g.include_archived,
                resource_paths=getattr(g, "resource_paths", ()),
            )
            for g in work
        )
        kinds = tuple(sorted({kind for g in work for kind in g.work_kinds}))
        rows = storage.delegated_arena_member_page(
            identifier,
            selectors=selectors,
            work_kinds=kinds,
            include_archived=True,
            after_resource=fields.get("after_resource", [None])[0],
            limit=limit,
        )
        repo = WorkObjectRepository(storage)
        return {"items": [todo_summary(repo._from_node(r.node)) for r in rows]}
