from __future__ import annotations

from dataclasses import replace

import pytest

from devgraph.model.base import GENERIC_STATUS_TRANSITIONS, WorkStatus
from devgraph.model.repository import (
    WORK_OBJECT_TYPES,
    InvalidStatusTransitionError,
    MissingWorkObjectError,
    UnknownWorkObjectKindError,
    WorkObjectAlreadyExistsError,
    WorkObjectRepository,
    WorkObjectRepositoryError,
    WorkObjectVersionConflictError,
)
from devgraph.model.validation import SIGNED_64_MAX, NumericBoundError
from devgraph.model.work import Decision, Proposal, Task
from devgraph.storage.base import NodeRecord
from devgraph.storage.memory import MemoryGraphStorage


@pytest.fixture()
def storage() -> MemoryGraphStorage:
    return MemoryGraphStorage()


@pytest.fixture()
def repository(storage: MemoryGraphStorage) -> WorkObjectRepository:
    return WorkObjectRepository(storage)


def _store(storage: MemoryGraphStorage, work_object):
    storage.create_node(work_object.kind, work_object.id, work_object.to_node_properties())
    return work_object


def _inject_persisted_node(
    storage: MemoryGraphStorage,
    label: str,
    node_id: str,
    properties: dict[str, object],
    *,
    archived: bool = False,
) -> None:
    """Bypass public write validation to simulate corrupt/future persisted data."""
    storage._nodes[(label, node_id)] = NodeRecord(
        label=label, id=node_id, properties=properties, archived=archived
    )


@pytest.mark.parametrize("kind", sorted(WORK_OBJECT_TYPES))
def test_get_by_id_round_trips_every_work_object_kind(storage, repository, kind):
    work_class = WORK_OBJECT_TYPES[kind]
    stored = _store(
        storage,
        work_class(
            id=f"{kind.lower()}-1",
            title=f"{kind} title",
            description="round-trip",
            artifact_ids=("artifact-1",),
            external_link_ids=("link-1",),
            priority=3,
        ),
    )

    loaded = repository.get_by_id(kind, stored.id)

    assert type(loaded) is work_class
    assert loaded == stored


def test_get_by_id_preserves_status_version_and_timestamps(storage, repository):
    proposal = Proposal(id="proposal-1", title="Preserve fields")
    accepted = proposal.with_status(WorkStatus.ACCEPTED)
    _store(storage, accepted)

    loaded = repository.get_by_id("Proposal", "proposal-1")

    assert loaded.status == WorkStatus.ACCEPTED
    assert loaded.version == accepted.version
    assert loaded.created_at == accepted.created_at
    assert loaded.updated_at == accepted.updated_at


def test_get_by_id_missing_node_fails_closed(repository):
    with pytest.raises(MissingWorkObjectError):
        repository.get_by_id("Task", "task-missing")


def test_get_by_id_unknown_requested_kind_fails_closed(storage, repository):
    storage.create_node("Widget", "widget-1", {"kind": "Widget", "title": "not a work object"})

    with pytest.raises(UnknownWorkObjectKindError):
        repository.get_by_id("Widget", "widget-1")


def test_unknown_future_persisted_properties_are_excluded(repository, storage) -> None:
    task = Task(id="task-future", title="Forward compatible")
    properties = task.to_node_properties()
    properties["future_field"] = "must-not-leak"
    _inject_persisted_node(storage, "Task", task.id, properties)

    hydrated = repository.get_by_id("Task", task.id)

    assert hydrated == task
    assert "future_field" not in hydrated.to_node_properties()


def test_unknown_persisted_kind_fails_closed_with_no_partial_object(storage, repository):
    task = Task(id="task-1", title="Corrupted kind")
    properties = task.to_node_properties()
    properties["kind"] = "Widget"
    _inject_persisted_node(storage, "Task", task.id, properties)

    with pytest.raises(UnknownWorkObjectKindError):
        repository.get_by_id("Task", task.id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", 1),
        ("description", False),
        ("status", "unknown"),
        ("created_at", "2026-01-01T00:00:00"),
        ("updated_at", "not-a-time"),
        ("version", True),
        ("version", 0),
        ("version", 9223372036854775808),
        ("artifact_ids", "not-a-list"),
        ("artifact_ids", ["ok", 1]),
        ("external_link_ids", ["BAD"]),
        ("priority", False),
        ("priority", -9223372036854775809),
    ],
)
def test_hydration_rejects_malformed_required_properties_without_coercion(
    storage, repository, field, value
):
    task = Task(id="task-1", title="Strict")
    properties = task.to_node_properties()
    properties[field] = value
    _inject_persisted_node(storage, "Task", task.id, properties)

    with pytest.raises(WorkObjectRepositoryError, match="unreadable properties"):
        repository.get_by_id("Task", task.id)


def test_hydration_rejects_missing_required_property(storage, repository):
    task = Task(id="task-1", title="Strict")
    properties = task.to_node_properties()
    del properties["description"]
    _inject_persisted_node(storage, "Task", task.id, properties)

    with pytest.raises(WorkObjectRepositoryError, match="unreadable properties"):
        repository.get_by_id("Task", task.id)


def test_hydration_rejects_archive_status_mismatch(storage, repository):
    task = Task(id="task-1", title="Strict")
    _inject_persisted_node(
        storage, "Task", task.id, task.to_node_properties(), archived=True
    )

    with pytest.raises(WorkObjectRepositoryError, match="archive status mismatch"):
        repository.get_by_id("Task", task.id)


def test_missing_persisted_kind_fails_closed(storage, repository):
    task = Task(id="task-2", title="Missing kind")
    properties = task.to_node_properties()
    del properties["kind"]
    _inject_persisted_node(storage, "Task", task.id, properties)

    with pytest.raises(UnknownWorkObjectKindError):
        repository.get_by_id("Task", task.id)


def test_kind_label_mismatch_fails_closed(storage, repository):
    task = Task(id="task-3", title="Mismatched label")
    properties = task.to_node_properties()
    properties["kind"] = "Proposal"
    _inject_persisted_node(storage, "Task", task.id, properties)

    with pytest.raises(UnknownWorkObjectKindError):
        repository.get_by_id("Task", task.id)


def test_query_filters_by_kind_and_excludes_archived_by_default(storage, repository):
    first = _store(storage, Task(id="task-1", title="First"))
    second = _store(storage, Task(id="task-2", title="Second"))
    _store(storage, Proposal(id="proposal-1", title="Other kind"))
    repository.archive("Task", second.id)

    active = repository.query("Task")
    everything = repository.query("Task", include_archived=True)

    assert [item.id for item in active] == [first.id]
    assert [item.id for item in everything] == [first.id, second.id]
    assert all(type(item) is Task for item in everything)


def test_query_uses_canonical_id_keyset_pages_in_both_directions(storage, repository):
    for identifier in ("b", "a--b", "a", "z"):
        _store(storage, Task(id=identifier, title=identifier))

    first = repository.query("Task", limit=2)
    second = repository.query("Task", after_id=first[-1].id, limit=2)
    reverse_first = repository.query("Task", descending=True, limit=2)
    reverse_second = repository.query(
        "Task", descending=True, after_id=reverse_first[-1].id, limit=2
    )

    assert [item.id for item in first] == ["a", "a--b"]
    assert [item.id for item in second] == ["b", "z"]
    assert [item.id for item in reverse_first] == ["z", "b"]
    assert [item.id for item in reverse_second] == ["a--b", "a"]


def test_query_rejects_invalid_keyset_inputs_before_storage_access() -> None:
    from devgraph.model.validation import InvalidWorkObjectId, NumericBoundError

    class SpyStorage(MemoryGraphStorage):
        calls = 0

        def query(self, *args, **kwargs):
            self.calls += 1
            return super().query(*args, **kwargs)

    storage = SpyStorage()
    repository = WorkObjectRepository(storage)
    with pytest.raises(InvalidWorkObjectId):
        repository.query("Task", after_id="BAD")
    for limit in (0, 101, True):
        with pytest.raises(NumericBoundError):
            repository.query("Task", limit=limit)
    with pytest.raises(TypeError, match="descending must be boolean"):
        repository.query("Task", descending=1)
    assert storage.calls == 0


def test_query_unknown_kind_fails_closed(repository):
    with pytest.raises(UnknownWorkObjectKindError):
        repository.query("Widget")


def test_create_persists_and_round_trips(storage, repository):
    task = Task(id="task-1", title="Create me", priority=2)

    created = repository.create(task)

    assert created == task
    assert repository.get_by_id("Task", "task-1") == task


def test_create_duplicate_fails_closed(repository):
    repository.create(Task(id="task-1", title="First"))

    with pytest.raises(WorkObjectAlreadyExistsError):
        repository.create(Task(id="task-1", title="Second"))


def test_create_normalizes_storage_uniqueness_race_to_repository_conflict():
    class CreateRaceStorage(MemoryGraphStorage):
        def create_node(self, label, node_id, properties=None):
            raise KeyError("storage-private uniqueness detail")

    repository = WorkObjectRepository(CreateRaceStorage())

    with pytest.raises(WorkObjectAlreadyExistsError) as excinfo:
        repository.create(Task(id="task-race", title="Racing writer"))

    assert "storage-private uniqueness detail" not in str(excinfo.value)


def test_create_unknown_kind_fails_closed(repository):
    class Widget(Task):
        pass

    with pytest.raises(UnknownWorkObjectKindError):
        repository.create(Widget(id="widget-1", title="Not a work class"))


def test_create_rejects_archived_status_side_door(repository):
    archived = Task(id="task-1", title="Archive side door", status=WorkStatus.ARCHIVED)

    with pytest.raises(InvalidStatusTransitionError):
        repository.create(archived)

    with pytest.raises(MissingWorkObjectError):
        repository.get_by_id("Task", "task-1")


def test_create_rejects_accepted_proposal_side_door(repository):
    accepted = Proposal(
        id="proposal-1",
        title="Accepted without provenance",
        status=WorkStatus.ACCEPTED,
    )

    with pytest.raises(InvalidStatusTransitionError):
        repository.create(accepted)

    with pytest.raises(MissingWorkObjectError):
        repository.get_by_id("Proposal", "proposal-1")


def test_update_applies_bump_semantics_and_preserves_status_and_created_at(storage, repository):
    task = repository.create(Task(id="task-1", title="Before", priority=1))

    updated = repository.update(
        replace(task, title="After", description="edited", priority=5),
        expected_version=1,
    )

    assert updated.title == "After"
    assert updated.description == "edited"
    assert updated.priority == 5
    assert updated.version == 2
    assert updated.status == task.status
    assert updated.created_at == task.created_at
    assert updated.updated_at >= task.updated_at
    assert repository.get_by_id("Task", "task-1") == updated


def test_update_stale_version_raises_safe_conflict_with_zero_mutation(storage, repository):
    task = repository.create(Task(id="task-1", title="Original"))
    repository.update(replace(task, title="Second write"), expected_version=1)

    with pytest.raises(WorkObjectVersionConflictError) as excinfo:
        repository.update(replace(task, title="Stale write"), expected_version=1)

    assert excinfo.value.expected_version == 1
    assert excinfo.value.actual_version == 2
    assert "Stale write" not in str(excinfo.value)
    current = repository.get_by_id("Task", "task-1")
    assert current.title == "Second write"
    assert current.version == 2


def test_update_cannot_change_status(repository):
    task = repository.create(Task(id="task-1", title="Status locked"))

    with pytest.raises(InvalidStatusTransitionError):
        repository.update(replace(task, status=WorkStatus.REVIEW), expected_version=1)

    assert repository.get_by_id("Task", "task-1").status == WorkStatus.DRAFT


def test_archived_work_object_is_immutable_through_update_paths(storage, repository):
    from devgraph.model.repository import ContentChanges

    task = repository.create(Task(id="task-archived", title="Before"))
    archived = repository.archive(task.kind, task.id)
    before = storage.get_node(task.kind, task.id)

    with pytest.raises(InvalidStatusTransitionError, match="archived"):
        repository.update(replace(archived, title="Mutated"), expected_version=2)
    with pytest.raises(InvalidStatusTransitionError, match="archived"):
        repository.update_content(
            task.kind,
            task.id,
            expected_version=2,
            changes=ContentChanges(title="Mutated"),
        )

    assert storage.get_node(task.kind, task.id) == before


def test_update_missing_work_object_fails_closed(repository):
    with pytest.raises(MissingWorkObjectError):
        repository.update(Task(id="task-missing", title="Ghost"), expected_version=1)


@pytest.mark.parametrize("operation", ["update", "archive", "status"])
def test_max_version_operation_fails_before_storage_mutation_or_timestamp_change(
    storage, repository, operation
):
    task = Task(id="task-1", title="At max", version=SIGNED_64_MAX)
    storage.create_node(task.kind, task.id, task.to_node_properties())
    before = storage.get_node(task.kind, task.id)

    with pytest.raises(NumericBoundError, match="invalid_version"):
        if operation == "update":
            repository.update(replace(task, title="changed"), expected_version=SIGNED_64_MAX)
        elif operation == "archive":
            repository.archive(task.kind, task.id)
        else:
            repository.transition_status(task.kind, task.id, WorkStatus.REVIEW)

    assert storage.get_node(task.kind, task.id) == before


def test_archive_is_one_storage_level_cas_write() -> None:
    class ArchiveSpyStorage(MemoryGraphStorage):
        update_calls = 0
        archive_properties = None

        def update_node(self, label, node_id, properties):
            self.update_calls += 1
            return super().update_node(label, node_id, properties)

        def archive_node(self, label, node_id, properties=None):
            self.archive_properties = properties
            return super().archive_node(label, node_id, properties)

    storage = ArchiveSpyStorage()
    repository = WorkObjectRepository(storage)
    repository.create(Task(id="task-1", title="Archive me"))

    archived = repository.archive("Task", "task-1")

    assert storage.update_calls == 0
    assert storage.archive_properties == archived.to_node_properties()


def test_archive_bumps_version_and_sets_storage_archived_flag(storage, repository):
    repository.create(Task(id="task-1", title="Archive me"))

    archived = repository.archive("Task", "task-1")

    assert archived.status == WorkStatus.ARCHIVED
    assert archived.version == 2
    node = storage.get_node("Task", "task-1")
    assert node is not None and node.archived is True
    assert node.properties["status"] == WorkStatus.ARCHIVED.value


def test_archive_twice_fails_closed(repository):
    repository.create(Task(id="task-1", title="Archive once"))
    repository.archive("Task", "task-1")

    with pytest.raises(InvalidStatusTransitionError):
        repository.archive("Task", "task-1")


def test_archive_rolls_back_completely_on_injected_storage_fault(repository, storage):
    class FaultyStorage(MemoryGraphStorage):
        def archive_node(self, label, node_id, properties=None):
            raise RuntimeError("injected storage fault")

    faulty = FaultyStorage()
    faulty_repository = WorkObjectRepository(faulty)
    task = faulty_repository.create(Task(id="task-1", title="Fault target"))

    with pytest.raises(RuntimeError):
        faulty_repository.archive("Task", "task-1")

    current = faulty_repository.get_by_id("Task", "task-1")
    assert current == task
    node = faulty.get_node("Task", "task-1")
    assert node is not None and node.archived is False


def test_transition_table_is_exactly_the_pinned_non_terminal_set():
    assert GENERIC_STATUS_TRANSITIONS == frozenset(
        {
            (WorkStatus.DRAFT, WorkStatus.REVIEW),
            (WorkStatus.REVIEW, WorkStatus.DRAFT),
            (WorkStatus.DRAFT, WorkStatus.ACCEPTED),
            (WorkStatus.REVIEW, WorkStatus.ACCEPTED),
        }
    )


def test_transition_status_follows_the_table(repository):
    repository.create(Task(id="task-1", title="Move me"))

    in_review = repository.transition_status("Task", "task-1", WorkStatus.REVIEW)
    assert in_review.status == WorkStatus.REVIEW
    assert in_review.version == 2

    accepted = repository.transition_status("Task", "task-1", WorkStatus.ACCEPTED)
    assert accepted.status == WorkStatus.ACCEPTED
    assert accepted.version == 3


def test_transition_status_rejects_proposal_accepted_side_door(repository):
    repository.create(Proposal(id="proposal-1", title="Needs provenance"))

    with pytest.raises(InvalidStatusTransitionError):
        repository.transition_status("Proposal", "proposal-1", WorkStatus.ACCEPTED)

    assert repository.get_by_id("Proposal", "proposal-1").status == WorkStatus.DRAFT


def test_transition_status_rejects_archived_target(repository):
    repository.create(Task(id="task-1", title="No archive side door"))

    with pytest.raises(InvalidStatusTransitionError):
        repository.transition_status("Task", "task-1", WorkStatus.ARCHIVED)


def test_transition_status_rejects_exits_from_terminal_statuses(repository):
    repository.create(Decision(id="decision-1", title="Already accepted"))
    repository.create(Task(id="task-1", title="To be archived"))
    repository.archive("Task", "task-1")

    with pytest.raises(InvalidStatusTransitionError):
        repository.transition_status("Decision", "decision-1", WorkStatus.DRAFT)
    with pytest.raises(InvalidStatusTransitionError):
        repository.transition_status("Task", "task-1", WorkStatus.DRAFT)


def test_transition_status_rejects_same_status_no_op(repository):
    repository.create(Task(id="task-1", title="No self loop"))

    with pytest.raises(InvalidStatusTransitionError):
        repository.transition_status("Task", "task-1", WorkStatus.DRAFT)


def test_update_content_merges_omissions_and_rejects_stale_without_mutation() -> None:
    from devgraph.model.repository import (
        ContentChanges,
        WorkObjectRepository,
        WorkObjectVersionConflictError,
    )
    from devgraph.model.work import Issue
    from devgraph.storage.memory import MemoryGraphStorage

    storage = MemoryGraphStorage()
    repo = WorkObjectRepository(storage)
    original = repo.create(Issue(id="atomic-1", title="Old", description="keep"))
    updated = repo.update_content(
        "Issue", "atomic-1", expected_version=1, changes=ContentChanges(title="New")
    )
    assert (
        updated.title == "New"
        and updated.description == "keep"
        and updated.status == original.status
    )
    with pytest.raises(WorkObjectVersionConflictError):
        repo.update_content(
            "Issue", "atomic-1", expected_version=1, changes=ContentChanges(title="bad")
        )
    assert repo.get_by_id("Issue", "atomic-1").title == "New"


def test_repository_rejects_invalid_identifiers_before_storage_access() -> None:
    from devgraph.model.validation import InvalidWorkObjectId

    class SpyStorage(MemoryGraphStorage):
        calls = 0

        def get_node(self, label, node_id):
            self.calls += 1
            return super().get_node(label, node_id)

    storage = SpyStorage()
    repo = WorkObjectRepository(storage)
    for operation in (
        lambda: repo.get_by_id("Task", "BAD"),
        lambda: repo.archive("Task", "bad_underscore"),
        lambda: repo.transition_status("Task", "bad space", WorkStatus.REVIEW),
    ):
        with pytest.raises(InvalidWorkObjectId):
            operation()
    assert storage.calls == 0


def test_repository_rejects_invalid_expected_version_before_storage_access() -> None:
    from devgraph.model.repository import ContentChanges
    from devgraph.model.validation import NumericBoundError

    class SpyStorage(MemoryGraphStorage):
        calls = 0

        def get_node(self, label, node_id):
            self.calls += 1
            return super().get_node(label, node_id)

    storage = SpyStorage()
    repo = WorkObjectRepository(storage)
    task = Task(id="task-1", title="valid")
    for operation in (
        lambda: repo.update(task, expected_version=True),
        lambda: repo.update_content(
            "Task", "task-1", expected_version=0, changes=ContentChanges(title="x")
        ),
    ):
        with pytest.raises(NumericBoundError):
            operation()
    assert storage.calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"title": None},
        {"title": 1},
        {"description": False},
        {"priority": True},
        {"priority": "1"},
        {"artifact_ids": ["ok", 1]},
        {"artifact_ids": ["BAD_id"]},
        {"external_link_ids": ["bad space"]},
        {"external_link_ids": "not-a-list"},
        {"status": "review"},
        {},
    ],
)
def test_content_changes_reject_invalid_values_independently(changes) -> None:
    from devgraph.model.repository import ContentChanges

    with pytest.raises((TypeError, ValueError)):
        ContentChanges.from_mapping(changes)
