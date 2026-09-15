"""Arena persistence and containment. Authorization belongs to the signed receiver."""

from __future__ import annotations

from devgraph.arena_contract import Arena, ArenaMembership
from devgraph.arena_requests import ArenaRequest
from devgraph.model.base import WorkStatus, utc_now
from devgraph.model.repository import (
    MissingWorkObjectError,
    WorkObjectAlreadyExistsError,
    WorkObjectRepository,
    WorkObjectVersionConflictError,
)
from devgraph.model.validation import validate_page_limit, validate_work_object_id
from devgraph.storage.base import GraphStorage, StorageUnavailable, WorkContainment
from devgraph.storage.containment import validate_arena_page

WORK_KINDS = frozenset({"Proposal", "Initiative", "Project", "Issue", "Task"})
DIRECT_KINDS = frozenset({"Initiative", "Task"})
PARENT_KINDS = {"Project": "Initiative", "Issue": "Project", "Task": "Issue"}


class ArenaConflict(ValueError):
    """Safe membership/archival conflict, without graph content."""


class ArenaRepository:
    def __init__(self, storage: GraphStorage):
        self.storage = storage
        self.work = WorkObjectRepository(storage)

    @staticmethod
    def _from_node(node):
        try:
            if node.label != "Arena" or "id" in node.properties or "archived" in node.properties:
                raise ValueError("invalid Arena record")
            return Arena.model_validate(
                {**node.properties, "id": node.id, "archived": node.archived}, strict=True
            )
        except (ValueError, TypeError):
            raise StorageUnavailable("malformed Arena record") from None

    def get(self, arena_id: str) -> Arena:
        validate_work_object_id(arena_id)
        node = self.storage.get_node("Arena", arena_id)
        if node is None:
            raise MissingWorkObjectError(f"missing Arena: {arena_id}")
        return self._from_node(node)

    def current(self, reference) -> Arena:
        arena = self.get(reference["id"])
        if arena.version != reference["expected_version"]:
            raise WorkObjectVersionConflictError(
                "Arena",
                arena.id,
                expected_version=reference["expected_version"],
                actual_version=arena.version,
            )
        return arena

    def list(self, *, include_archived=False, after_id=None, limit=50) -> list[Arena]:
        validate_page_limit(limit)
        if type(include_archived) is not bool:
            raise ValueError("invalid archive filter")
        if after_id is not None:
            validate_work_object_id(after_id)
        return [
            self._from_node(node)
            for node in self.storage.query(
                "Arena",
                archived=None if include_archived else False,
                after_id=after_id,
                limit=limit,
            )
        ]

    def _containment(self, kind: str, work_id: str) -> WorkContainment:
        item = self.storage.work_containment(kind, work_id)
        if item is None:
            raise MissingWorkObjectError(f"missing {kind}: {work_id}")
        self.work._from_node(item.node)
        return item

    @staticmethod
    def _direct_id(item: WorkContainment) -> str | None:
        incoming = item.memberships
        if len(incoming) > 1 or any(e.from_label != "Arena" for e in incoming):
            raise ArenaConflict("invalid direct Arena membership")
        if incoming and item.node.label not in DIRECT_KINDS:
            raise ArenaConflict("ineligible direct Arena member")
        return incoming[0].from_id if incoming else None

    def direct(self, kind, work_id):
        arena_id = self._direct_id(self._containment(kind, work_id))
        return self.get(arena_id) if arena_id is not None else None

    def membership(self, kind: str, work_id: str) -> ArenaMembership:
        if kind not in WORK_KINDS:
            raise ValueError("invalid Work kind")
        original = current = (kind, work_id)
        visited = set()
        while True:
            if current in visited:
                raise ArenaConflict("cyclic Work parentage")
            visited.add(current)
            item = self._containment(*current)
            incoming = item.parents
            if len(incoming) > 1:
                raise ArenaConflict("multiple Work parents")
            arena_id = self._direct_id(item)
            if not incoming:
                return ArenaMembership(
                    arena=self.get(arena_id) if arena_id is not None else None,
                    root_kind=current[0],
                    root_id=current[1],
                    inherited=arena_id is not None and current != original,
                )
            edge = incoming[0]
            if arena_id is not None or edge.from_label != PARENT_KINDS.get(current[0]):
                raise ArenaConflict("invalid Arena membership or Work parentage")
            current = (edge.from_label, edge.from_id)

    def members(self, arena_id: str, *, after_resource=None, limit=50):
        validate_arena_page(arena_id, after_resource, limit)
        self.get(arena_id)
        result = []
        for item in self.storage.arena_member_page(
            arena_id, after_resource=after_resource, limit=limit
        ):
            # A direct page contains roots only. Its bounded incoming-edge witnesses
            # validate this without resolving and refetching each root separately.
            if item.parents or self._direct_id(item) != arena_id:
                raise ArenaConflict("invalid direct Arena member")
            result.append(self.work._from_node(item.node))
        return result

    def bump(self, arena: Arena, **changes) -> Arena:
        updated = Arena.model_validate(
            {
                **arena.model_dump(),
                **changes,
                "version": arena.version + 1,
                "updated_at": utc_now().isoformat(),
            },
            strict=True,
        )
        properties = updated.model_dump(exclude={"id", "archived"})
        if updated.archived and not arena.archived:
            self.storage.archive_node("Arena", arena.id, properties)
        else:
            self.storage.update_node("Arena", arena.id, properties)
        return updated

    def remove_for_parent(self, child, previous_arena):
        direct = self.direct(child.kind, child.id)
        if (None if direct is None else direct.id) != (
            None if previous_arena is None else previous_arena["id"]
        ):
            raise ArenaConflict("Arena parent-change precondition failed")
        if direct is not None:
            self.membership(child.kind, child.id)  # Reject already-invalid parentage.
            arena = self.current(previous_arena)
            self.storage.delete_edge("Arena", arena.id, "CONTAINS_WORK", child.kind, child.id)
            self.bump(arena)


class ArenaMutations:
    def __init__(self, storage: GraphStorage):
        self.storage = storage
        self.repository = ArenaRepository(storage)

    def execute(self, request: ArenaRequest):
        request = ArenaRequest.from_json(request.canonical)
        with self.storage.work_mutation_transaction():
            return self._execute(request)

    def _execute(self, request):
        payload = request.payload
        if request.operation == "create":
            now = utc_now().isoformat()
            arena = Arena(
                **payload,
                schema_version=1,
                kind="Arena",
                version=1,
                archived=False,
                created_at=now,
                updated_at=now,
            )
            try:
                self.storage.create_node(
                    "Arena", arena.id, arena.model_dump(exclude={"id", "archived"})
                )
            except KeyError:
                raise WorkObjectAlreadyExistsError("Arena already exists") from None
            return arena
        if request.operation == "member.set":
            return self._member(request)
        arena = self.repository.current(
            {"id": request.id, "expected_version": request.expected_version}
        )
        if arena.archived:
            raise ArenaConflict("archived Arena cannot be changed")
        return self.repository.bump(
            arena, **({"archived": True} if request.operation == "archive" else payload)
        )

    def _member(self, request):
        member = self.repository.work.get_by_id(request.kind, request.id)
        if member.version != request.expected_version:
            raise WorkObjectVersionConflictError(
                member.kind,
                member.id,
                expected_version=request.expected_version,
                actual_version=member.version,
            )
        if member.status == WorkStatus.ARCHIVED:
            raise ArenaConflict("archived Work cannot change membership")
        membership = self.repository.membership(member.kind, member.id)
        if (membership.root_kind, membership.root_id) != (member.kind, member.id):
            raise ArenaConflict("direct Arena member must be parentless")
        prior, target = request.payload["previous_arena"], request.payload["arena"]
        if (None if membership.arena is None else membership.arena.id) != (
            None if prior is None else prior["id"]
        ):
            raise ArenaConflict("Arena membership precondition failed")
        touched = {}
        for reference in (prior, target):
            if reference is not None:
                arena = self.repository.current(reference)
                touched[arena.id] = arena
        if target is not None and touched[target["id"]].archived:
            raise ArenaConflict("cannot assign to an archived Arena")
        if prior is not None:
            self.storage.delete_edge("Arena", prior["id"], "CONTAINS_WORK", member.kind, member.id)
        if target is not None:
            self.storage.create_edge("Arena", target["id"], "CONTAINS_WORK", member.kind, member.id)
        for arena in touched.values():
            self.repository.bump(arena)
        return self.repository.work.update(member, expected_version=member.version)
