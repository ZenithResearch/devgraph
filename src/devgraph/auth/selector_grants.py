"""Provider-owned v2 selector bridge. Never trusts ancestry supplied by a client.

Legacy constraints are retained and intersected with shared secS selectors. This
adapter materializes only a transaction-local read view, never a durable grant.
"""

from __future__ import annotations

from dataclasses import dataclass

from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.holder_grants import invoke_native
from devgraph.resource_paths import current_resource_path
from devgraph.storage.base import StorageUnavailable
from devgraph.storage.delegated import DelegatedWorkSelector

RELATION = "devgraph.containment.v1"


def permission_v2(value, service):
    import json

    p = json.loads(canonical_json(value))
    c = p["constraints"]
    arena = c.get("scope") == "devgraph.arena.read"
    kinds = ["Arena"] if arena else list(c.get("work_kinds", ()))
    if not kinds:
        raise UnauthenticatedError("unsupported_selector_kind")
    ids = ["Arena/" + i for i in c.get("arena_ids", ())] if arena else c.get("work_ids", ())
    selection = {"mode": "all"}
    if ids and len(ids) <= 128:
        selection = {
            "mode": "objects",
            "refs": [dict(zip(("kind", "id"), i.split("/", 1), strict=True)) for i in ids],
        }
    elif c.get("resource_paths"):
        # The original conjunctive path constraints remain in the signed record.
        # The selector is an additional intersection, never a replacement.
        roots = []
        for path in c["resource_paths"]:
            r = dict(zip(("kind", "id"), path["path"][-1].split("/", 1), strict=True))
            if r not in roots:
                roots.append(r)
        selection = {
            "mode": "descendants",
            "hierarchy": {"relation": RELATION, "roots": roots, "include_self": True},
        }
    p["roles"] = [
        {
            "role": "target",
            "selector": {
                "schema": "secs.resource-selector.v1",
                "service": service,
                "kinds": kinds,
                "selection": selection,
            },
        }
    ]
    return p


@dataclass(frozen=True)
class SelectorGrant:
    grant: object
    selector: dict
    host: object

    def __getattr__(self, name):
        return getattr(self.grant, name)

    def model_copy(self, **kwargs):
        return SelectorGrant(self.grant.model_copy(**kwargs), self.selector, self.host)


def wrap_permission(host, permission, decoded):
    if set(permission) != {"operation", "resource", "roles", "constraints"}:
        raise UnauthenticatedError("unsupported_selector_permission")
    roles = permission["roles"]
    if len(roles) != 1 or set(roles[0]) != {"role", "selector"} or roles[0]["role"] != "target":
        raise UnauthenticatedError("unsupported_selector_roles")
    return tuple(SelectorGrant(g, roles[0]["selector"], host) for g in decoded)


def snapshot(storage, service, refs):
    rows = []
    for kind, identifier in sorted(set(refs)):
        node = storage.get_node(kind, identifier)
        if node is None:
            continue
        canonical_kind = node.properties.get("kind")
        compatible_session = kind in {"SessionRef", "SessionLink"} and canonical_kind is None
        if (
            node.label != kind
            or node.id != identifier
            or (canonical_kind != kind and not compatible_session)
        ):
            raise UnauthenticatedError("ambiguous_resource_identity")
        if kind == "Arena":
            ancestors = []
        elif kind in __import__("devgraph.progress", fromlist=["TODO_KINDS"]).TODO_KINDS:
            path = current_resource_path(storage, kind, identifier)
            if path is None:
                raise UnauthenticatedError("ambiguous_resource_hierarchy")
            ancestors = [
                dict(zip(("kind", "id"), p.split("/", 1), strict=True))
                for p in path
                if p != kind + "/" + identifier
            ]
        else:
            ancestors = []
        rows.append({"resource": {"kind": kind, "id": identifier}, "ancestors": ancestors})
    return {"service": service, "relation": RELATION, "resources": rows}


def permitted(storage, wrapper, nodes):
    selector, host = wrapper.selector, wrapper.host
    selection = selector["selection"]
    h = selection.get("hierarchy", selection.get("within"))
    roots = [(r["kind"], r["id"]) for r in h["roots"]] if h else []
    result = []
    for offset in range(0, len(nodes), 16):
        page = nodes[offset : offset + 16]
        targets = [{"kind": n.label, "id": n.id} for n in page]
        state = snapshot(storage, host.service, roots + [(n.label, n.id) for n in page])
        checked = invoke_native(
            host.binary,
            host.binary_sha256,
            host.pin,
            "check_selector",
            selector=selector,
            targets=targets,
            snapshot=state,
        )
        allowed = checked.get("allowed")
        if (
            not isinstance(allowed, list)
            or len(allowed) != len(page)
            or any(type(a) is not bool for a in allowed)
        ):
            raise UnauthenticatedError("invalid_selector_result")
        result.extend(n for n, allow in zip(page, allowed, strict=True) if allow)
    return result


def materialize(storage, grants):
    """Called only under the graph's mutation transaction, before any disclosure."""
    result = []
    for g in grants:
        if not isinstance(g, SelectorGrant):
            result.append(g)
            continue
        if g.selector["selection"] == {"mode": "all"}:
            if g.scope == "devgraph.arena.read" and "Arena" in g.selector["kinds"]:
                result.append(g.grant)
            elif g.scope == "devgraph.work.read":
                kinds = tuple(k for k in g.work_kinds if k in g.selector["kinds"])
                if kinds:
                    result.append(g.grant.model_copy(update={"work_kinds": kinds}))
            else:
                raise UnauthenticatedError("unsupported_selector_operation")
            continue
        nodes = []
        if g.scope == "devgraph.arena.read":
            after = None
            while True:
                page = storage.delegated_arena_page(
                    getattr(g, "arena_ids", ()),
                    all_resources=getattr(g, "all_arenas", False),
                    after_id=after,
                    limit=100,
                )
                nodes.extend(page)
                if len(nodes) > 10000:
                    raise StorageUnavailable("selector read capacity exceeded")
                if len(page) < 100:
                    break
                after = page[-1].id
            selected = permitted(storage, g, nodes)
            if selected:
                from devgraph.auth.arena_access import VerifiedArenaReadGrant

                # Explicit Arena IDs are a transaction-local intersection.
                result.append(
                    VerifiedArenaReadGrant(
                        scope="devgraph.arena.read",
                        arena_ids=tuple(n.id for n in selected),
                        operations=getattr(g, "operations", ("list", "get")),
                    )
                )
        elif g.scope == "devgraph.work.read":
            selector = DelegatedWorkSelector(
                work_ids=g.work_ids,
                arena_ids=g.arena_ids,
                work_kinds=g.work_kinds,
                include_archived=g.include_archived,
                resource_paths=getattr(g, "resource_paths", ()),
            )
            for kind in g.work_kinds:
                after = None
                while True:
                    page = storage.delegated_work_page(
                        kind,
                        selectors=(selector,),
                        work_kinds=(kind,),
                        include_archived=True,
                        descending=False,
                        after_id=after,
                        limit=100,
                    )
                    nodes.extend(page)
                    if len(nodes) > 10000:
                        raise StorageUnavailable("selector read capacity exceeded")
                    if len(page) < 100:
                        break
                    after = page[-1].id
            selected = permitted(storage, g, nodes)
            if selected:
                result.append(
                    g.grant.model_copy(
                        update={"work_ids": tuple(n.label + "/" + n.id for n in selected)}
                    )
                )
        else:
            raise UnauthenticatedError("unsupported_selector_operation")
    return tuple(result)
