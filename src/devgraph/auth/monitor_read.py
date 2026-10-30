"""Versioned six-kind read scope and read-only projections for Wallet clients.

The historical five-kind contract remains closed. Projections capture authorized
nodes at storage level, then derive counts and cursors from that exact inventory.
No application credential or ordinary read scope is synthesized from a signature.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from devgraph.auth.access_policy import AccessDraft, MemberAccessPolicy
from devgraph.auth.delegated_contract import (
    RELATIONSHIP_TYPE_ORDER,
    WORK_OPERATION_ORDER,
    WORK_RELATIONSHIPS,
    WorkReadGrant,
    _ordered_unique,
    _selector,
    canonical_json,
)
from devgraph.auth.errors import ForbiddenError, UnauthenticatedError
from devgraph.auth.member_read import MemberReadEnrollment
from devgraph.auth.native_read import EnrollmentPolicy
from devgraph.progress import TODO_KINDS
from devgraph.storage.base import StorageUnavailable
from devgraph.storage.delegated import DelegatedWorkSelector, validate_selectors
from devgraph.todo_views import (
    TodoFilters,
    classification_report,
    todo_detail,
    todo_page,
    todo_summary,
)
from devgraph.topology import TopologyFilter, build_topology, revalidate_topology


class MonitorWorkGrant(WorkReadGrant):
    work_kinds: tuple[Literal["Todo", "Proposal", "Initiative", "Project", "Issue", "Task"], ...]

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value):
        if not isinstance(value, dict):
            return value
        result = dict(value)
        for name, order in (
            ("operations", WORK_OPERATION_ORDER),
            ("relationship_types", RELATIONSHIP_TYPE_ORDER),
            ("work_kinds", TODO_KINDS),
        ):
            if name in result:
                result[name] = _ordered_unique(result[name], order, name)
        for name in ("work_ids", "arena_ids"):
            if name in result:
                result[name] = _selector(result[name], name)
        return result


class EnrollmentPolicyV2(EnrollmentPolicy):
    schema_: Literal["devgraph.native-read-policy.v2"] = Field(alias="schema")
    grants: tuple[MonitorWorkGrant, ...] = Field(min_length=1, max_length=16)
    lifetime_seconds: int = Field(strict=True, ge=60, le=3600)

    @model_validator(mode="after")
    def validate_policy(self):
        url = urlsplit(self.receiver)
        if (
            url.scheme != "https"
            or not url.netloc
            or url.path
            or url.query
            or url.fragment
            or url.username
            or url.password
        ):
            raise ValueError("receiver must be an HTTPS origin")
        if any(getattr(g, "resource_paths", ()) for g in self.grants):
            raise ValueError("resource paths require a new enrollment contract")
        if sum(len(g.operations) for g in self.grants) > 32:
            raise ValueError("too many read permissions")
        for grant in self.grants:
            validate_selectors(grant.work_ids, grant.arena_ids)
        return self


class AccessDraftV2(AccessDraft):
    schema_: Literal["devgraph.access-draft.v2"] = Field(alias="schema")
    read_grants: tuple[MonitorWorkGrant, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def valid(self):
        if any(getattr(g, "resource_paths", ()) for g in (*self.read_grants, *self.write_grants)):
            raise ValueError("resource paths require a new access draft contract")
        if self.not_before >= self.not_after or not (self.read_grants or self.write_grants):
            raise ValueError("a grant and valid interval are required")
        if self.installation_id is not None and self.subject_public_key is None:
            raise ValueError("installation binding requires a member")
        placeholder = MonitorWorkGrant(
            scope="devgraph.work.read",
            operations=("get",),
            relationship_types=("parent",),
            work_kinds=("Todo",),
            include_archived=False,
        )
        EnrollmentPolicyV2.model_validate(
            {
                "schema": "devgraph.native-read-policy.v2",
                "issuer": self.issuer,
                "receiver": self.receiver,
                "grants": self.read_grants or (placeholder,),
                "lifetime_seconds": self.lifetime_seconds,
            }
        )
        for grants in (self.read_grants, self.write_grants):
            if len({canonical_json(g) for g in grants}) != len(grants):
                raise ValueError("duplicate grant")
        return self


class MemberAccessPolicyV2(MemberAccessPolicy):
    schema_: Literal["devgraph.member-access-policy.v2"] = Field(alias="schema")
    ceiling: AccessDraftV2
    assignments: tuple[AccessDraftV2, ...] = Field(max_length=128)


def compile_monitor_read(draft):
    return EnrollmentPolicyV2.model_validate(
        {
            "schema": "devgraph.native-read-policy.v2",
            "issuer": draft.issuer,
            "receiver": draft.receiver,
            "grants": draft.read_grants,
            "lifetime_seconds": draft.lifetime_seconds,
        }
    )


class MemberMonitorReadEnrollment(MemberReadEnrollment):
    def _policy_for(self, owner, device, current):
        assignment = self._assignment(owner, device, current)
        if not assignment.read_grants:
            raise UnauthenticatedError("member_access_not_granted")
        return compile_monitor_read(assignment)

    def _details(self, policy):
        return {**super()._details(policy), "schema": "devgraph.native-read-scope.v3"}

    def _result(self, credential, manifest):
        return {**super()._result(credential, manifest), "schema": "devgraph.native-read-result.v3"}

    def _decode_enrollment_ceiling(self, grants):
        return tuple(MonitorWorkGrant.model_validate(grant) for grant in grants)

    def _decode_grants(self, permissions):
        result = {}
        for permission in permissions:
            grant = MonitorWorkGrant.model_validate(permission["constraints"])
            if permission["operation"].removeprefix("devgraph.work.") not in grant.operations:
                raise ValueError("invalid read permissions")
            result[canonical_json(grant)] = grant
        return tuple(result.values())


class ReadProjectionStorage:
    """Only the captured authorized inventory; contains no source storage handle."""

    def __init__(self, nodes, edges, health):
        self.nodes = tuple(nodes)
        self.edges = tuple(edges)
        self._health = health
        self.by_key = {(n.label, n.id): n for n in nodes}

    def query(self, label=None, archived=None, after_id=None, descending=False, limit=None):
        nodes = sorted(
            (
                n
                for n in self.nodes
                if (label is None or n.label == label)
                and (archived is None or n.archived == archived)
                and (after_id is None or (n.id < after_id if descending else n.id > after_id))
            ),
            key=lambda n: n.id,
            reverse=descending,
        )
        return nodes if limit is None else nodes[:limit]

    def get_node(self, label, node_id):
        return self.by_key.get((label, node_id))

    def list_edges(self, relationship=None, *, limit=None):
        result = [e for e in self.edges if relationship is None or e.relationship == relationship]
        return result if limit is None else result[:limit]

    def health(self):
        return self._health

    def monitor_records(self):
        return list(self.nodes), list(self.edges)


def monitor_target(target):
    if (
        not isinstance(target, str)
        or len(target) > 4096
        or "#" in target
        or "%" in target.partition("?")[0]
        or any(ord(c) < 32 for c in target)
    ):
        raise ValueError("unsupported_read_target")
    path, _, query = target.partition("?")
    from devgraph.auth.arena_projection import arena_target

    arena = arena_target(target)
    if arena is not None:
        return arena
    if path in (
        "/monitor/topology/v1",
        "/monitor/snapshot",
        "/monitor/kanban/v2",
        "/workflows/v1",
        "/todos/v2",
        "/todos/v2/classification-report",
    ):
        if query and path in (
            "/monitor/snapshot",
            "/todos/v2/classification-report",
            "/workflows/v1",
        ):
            raise ValueError("unsupported_read_target")
        return "list", path, query
    detail = re.fullmatch(
        r"/todos/v2/(Todo|Proposal|Initiative|Project|Issue|Task)/([a-z0-9][a-z0-9-]{0,255})", path
    )
    if detail and not query:
        return "get", path, query
    related = re.fullmatch(
        r"/work/(Todo|Proposal|Initiative|Project|Issue|Task)/([a-z0-9][a-z0-9-]{0,255})/relationships/(children|parent|dependencies|dependents|blockers|blocked)",
        path,
    )
    if related:
        return "relationships", path, query
    raise ValueError("unsupported_read_target")


def project_monitor_read(storage, grants, target):
    from devgraph.auth.arena_projection import project_arena_read

    if monitor_target(target)[0].startswith("arena."):
        return project_arena_read(storage, grants, target)
    grants = tuple(grants)
    # Capture every selector, page and edge in one serialized graph interval.
    with storage.work_mutation_transaction():
        return _project_monitor_read(storage, grants, target)


def _project_monitor_read(storage, grants, target):
    operation, path, query = monitor_target(target)
    arena_grants = [g for g in grants if g.scope == "devgraph.arena.read"]
    work_grants = [g for g in grants if g.scope == "devgraph.work.read"]
    grants = [g for g in work_grants if operation in g.operations]
    relationship_grants = [g for g in work_grants if "relationships" in g.operations]
    if not grants:
        raise ForbiddenError("scope_not_granted", correlation_id="monitor-read")
    by_key = {}
    # List/get and relationship permissions are independently decoded and may
    # have disjoint selectors. Reuse each selector capture without combining
    # their authority or reading the same inventory once per signed operation.
    pages = {}

    def selected_nodes(grant):
        selector = DelegatedWorkSelector(
            work_ids=grant.work_ids,
            resource_paths=getattr(grant, "resource_paths", ()),
            arena_ids=grant.arena_ids,
            work_kinds=grant.work_kinds,
            include_archived=grant.include_archived,
        )
        cache_key = (
            grant.work_ids, grant.arena_ids, getattr(grant, "resource_paths", ()),
            grant.work_kinds, grant.include_archived,
        )
        if cache_key in pages:
            return pages[cache_key]
        selected = []
        for kind in grant.work_kinds:
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
                selected.extend(page)
                if len(selected) > 10000:
                    raise StorageUnavailable("scoped read capacity exceeded")
                if len(page) < 100:
                    break
                after = page[-1].id
        pages[cache_key] = selected
        return selected

    for grant in grants:
        for node in selected_nodes(grant):
            by_key[node.label, node.id] = node
        if len(by_key) > 10000:
            raise StorageUnavailable("scoped read capacity exceeded")
    nodes = list(by_key.values())
    node_relationships = {}
    for grant in relationship_grants:
        for node in selected_nodes(grant):
            key = (node.label, node.id)
            if key in by_key:
                node_relationships.setdefault(key, set()).update(grant.relationship_types)
    relationships = set()
    relation_map = {name: definition[0] for name, definition in WORK_RELATIONSHIPS.items()}
    for allowed in node_relationships.values():
        relationships.update(relation_map[r] for r in allowed)
    edges = storage.delegated_work_edges(
        tuple((n.label, n.id) for n in nodes), tuple(sorted(relationships)), limit=10001
    )
    if len(edges) > 10000:
        raise StorageUnavailable("scoped relationship capacity exceeded")
    directions = {
        "HAS_CHILD": ("children", "parent"),
        "DEPENDS_ON": ("dependencies", "dependents"),
        "BLOCKS": ("blocked", "blockers"),
    }

    def permits(key, relationship):
        return relationship in node_relationships.get(key, ())

    edges = [
        e
        for e in edges
        if permits((e.from_label, e.from_id), directions[e.relationship][0])
        or permits((e.to_label, e.to_id), directions[e.relationship][1])
    ]
    arena_ids = ()
    dynamic_arenas = False
    if path in ("/monitor/topology/v1", "/monitor/snapshot", "/monitor/kanban/v2"):
        # Arena metadata never follows from a Work selector. Both endpoints of
        # each real membership edge must already be independently authorized.
        arena_ids = tuple(
            sorted(
                {
                    identifier
                    for grant in arena_grants
                    if "list" in getattr(grant, "operations", ("list", "get"))
                    for identifier in getattr(grant, "arena_ids", ())
                }
            )
        )
        dynamic_arenas = any(
            getattr(g, "all_arenas", False) is True
            and "list" in getattr(g, "operations", ("list", "get"))
            for g in arena_grants
        )
        if arena_ids or dynamic_arenas:
            after = None
            arena_nodes = []
            while True:
                page = storage.delegated_arena_page(
                    () if dynamic_arenas else arena_ids,
                    all_resources=dynamic_arenas,
                    after_id=after,
                    limit=100,
                )
                arena_nodes.extend(page)
                if len(page) < 100:
                    break
                after = page[-1].id
            disclosed_ids = tuple(n.id for n in arena_nodes)
            if disclosed_ids:
                edges.extend(
                    storage.delegated_arena_edges(
                        disclosed_ids, tuple((n.label, n.id) for n in nodes), limit=10001
                    )
                )
            nodes.extend(arena_nodes)
            if len(nodes) > 10000 or len(edges) > 10000:
                raise StorageUnavailable("scoped Arena projection capacity exceeded")
    captured = ReadProjectionStorage(nodes, edges, storage.health())
    if path == "/workflows/v1":
        from devgraph.workflow_contract import catalog

        return catalog()
    if path == "/monitor/kanban/v2":
        from devgraph.kanban import BoardFilter, build_board

        return build_board(captured, BoardFilter.parse(query), version=2)
    if path == "/todos/v2":
        return todo_page(captured, TodoFilters.parse(query))
    if path == "/todos/v2/classification-report":
        return classification_report(captured)
    if path.startswith("/todos/v2/"):
        _, _, _, kind, identifier = path.split("/")
        if captured.get_node(kind, identifier) is None:
            raise KeyError("not_found")
        return todo_detail(captured, kind, identifier)
    if path.startswith("/work/"):
        from urllib.parse import parse_qs

        from devgraph.model.repository import WorkObjectRepository

        fields = parse_qs(query, strict_parsing=True, max_num_fields=3) if query else {}
        if set(fields) - {"limit", "after_resource"} or any(len(v) != 1 for v in fields.values()):
            raise ValueError("invalid_read_target")
        limit = int(fields.get("limit", ["50"])[0])
        if not 1 <= limit <= 100:
            raise ValueError("invalid_read_target")
        _, _, kind, identifier, _, relationship = path.split("/")
        if captured.get_node(kind, identifier) is None:
            raise KeyError("not_found")
        if not permits((kind, identifier), relationship):
            raise ForbiddenError("scope_not_granted", correlation_id="monitor-read")
        incoming = WORK_RELATIONSHIPS[relationship][1]
        relation = relation_map[relationship]
        keys = sorted(
            {
                (e.from_label, e.from_id) if incoming else (e.to_label, e.to_id)
                for e in edges
                if e.relationship == relation
                and ((e.to_label, e.to_id) if incoming else (e.from_label, e.from_id))
                == (kind, identifier)
            }
        )
        after = fields.get("after_resource", [None])[0]
        keys = [k for k in keys if after is None or "/".join(k) > after][:limit]
        return {
            "items": [todo_summary(WorkObjectRepository(captured).get_by_id(*key)) for key in keys]
        }
    from devgraph.monitoring import build_monitor_snapshot

    result = (
        build_topology(captured, TopologyFilter.parse(query))
        if path == "/monitor/topology/v1"
        else build_monitor_snapshot(captured)
    )
    result["access_scope"] = {
        "schema": "devgraph.monitor-access-scope.v1",
        "work_kinds": sorted({k for g in grants for k in g.work_kinds}),
        "observations": False,
        "receipts": False,
        "supporting_material": False,
        "relationship_data": bool(relationships or arena_ids or dynamic_arenas),
        "work_relationships": bool(node_relationships),
        "arena_metadata": bool(arena_ids or dynamic_arenas),
        "arena_membership_complete": dynamic_arenas and all(
            "parent" in node_relationships.get(key, ()) for key in by_key
        ),
        "complete": True,
    }
    if path == "/monitor/topology/v1":
        return revalidate_topology(result, TopologyFilter.parse(query).if_revision)
    return result
