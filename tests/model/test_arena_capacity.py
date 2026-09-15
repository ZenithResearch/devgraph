"""Capacity regressions: graph growth must not disable local containment operations."""

from collections import Counter

import pytest
from tests.model.test_arena_runtime import add_arena, assign
from tests.model.test_named_work import command, ref

from devgraph.arenas import ArenaConflict, ArenaMutations, ArenaRepository
from devgraph.model.work import Issue, Task
from devgraph.named_work import NamedWorkMutations
from devgraph.storage.base import EdgeRecord
from devgraph.storage.memory import MemoryGraphStorage


class CountedStorage(MemoryGraphStorage):
    def __init__(self):
        super().__init__()
        self.reads = Counter()
        self.returned_edges = 0

    def get_node(self, *args, **kwargs):
        self.reads['node'] += 1
        return super().get_node(*args, **kwargs)

    def work_containment(self, *args, **kwargs):
        self.reads['containment'] += 1
        return super().work_containment(*args, **kwargs)

    def arena_member_page(self, *args, **kwargs):
        self.reads['page'] += 1
        result = super().arena_member_page(*args, **kwargs)
        self.returned_edges += sum(len(item.parents) + len(item.memberships) for item in result)
        return result

    def list_edges(self, *args, **kwargs):
        self.reads['global_inventory'] += 1
        return super().list_edges(*args, **kwargs)


@pytest.fixture
def large_arena():
    storage = CountedStorage()
    mutations = ArenaMutations(storage)
    add_arena(mutations)
    add_arena(mutations, 'other')
    # Bulk fixture setup avoids O(n²) transaction snapshots in the memory adapter.
    for number in range(10_001):
        task = Task(id=f'task-{number:05}', title='Capacity fixture')
        storage.create_node(task.kind, task.id, task.to_node_properties())
    issue = Issue(id='parent', title='Parent')
    storage.create_node(issue.kind, issue.id, issue.to_node_properties())
    storage._edges = [
        EdgeRecord('Arena', 'gallery', 'CONTAINS_WORK', 'Task', f'task-{number:05}')
        for number in range(10_000)
    ]
    storage.reads.clear()
    return storage, mutations


def test_crossing_old_global_limit_remains_readable_movable_and_removable(large_arena):
    storage, mutations = large_arena
    work = assign(mutations, 'Task', 'task-10000', 1, arena=ref('Arena', 'gallery', 1))
    assert len(storage._edges) == 10_001 and work.version == 2
    assert mutations.repository.membership('Task', work.id).arena.id == 'gallery'
    assert len(mutations.repository.members('gallery', limit=100)) == 100

    # Unrelated parent assignment also works while the old limit is exceeded.
    task = Task(id='unassigned', title='Unrelated')
    storage.create_node(task.kind, task.id, task.to_node_properties())
    parented = NamedWorkMutations(storage).execute(command('parent.set', 'Task', task.id, {
        'previous_parent': None, 'parent': ref('Issue', 'parent', 1),
    }, 1))
    assert parented.version == 2
    assert mutations.repository.membership('Task', task.id).arena is None

    moved = assign(mutations, 'Task', work.id, 2, previous=ref('Arena', 'gallery', 2),
                   arena=ref('Arena', 'other', 1))
    assert moved.version == 3
    assert mutations.repository.membership('Task', work.id).arena.id == 'other'
    removed = assign(mutations, 'Task', work.id, 3, previous=ref('Arena', 'other', 2))
    assert removed.version == 4
    assert mutations.repository.membership('Task', work.id).arena is None


def test_removal_and_signed_parent_assignment_recover_an_already_large_graph(large_arena):
    storage, mutations = large_arena
    storage._edges.append(EdgeRecord('Arena', 'gallery', 'CONTAINS_WORK', 'Task', 'task-10000'))
    before = list(storage._edges)
    # The omitted Arena still fails atomically, even beyond the old budget.
    payload = {'previous_parent': None, 'parent': ref('Issue', 'parent', 1)}
    with pytest.raises(ArenaConflict):
        NamedWorkMutations(storage).execute(command('parent.set', 'Task', 'task-10000', payload, 1))
    assert storage._edges == before
    assert mutations.repository.get('gallery').version == 1
    payload['previous_arena'] = ref('Arena', 'gallery', 1)
    assert NamedWorkMutations(storage).execute(
        command('parent.set', 'Task', 'task-10000', payload, 1)
    ).version == 2
    assert mutations.repository.membership('Task', 'task-10000').arena is None
    assert mutations.repository.get('gallery').version == 2
    assign(mutations, 'Task', 'task-00000', 1, previous=ref('Arena', 'gallery', 2))
    assert mutations.repository.membership('Task', 'task-00000').arena is None


@pytest.mark.parametrize('limit', [1, 50, 100])
def test_page_reads_are_constant_and_edges_are_bounded_by_page_size(large_arena, limit):
    storage, mutations = large_arena
    result = mutations.repository.members('gallery', after_resource='Task/task-09899', limit=limit)
    assert [item.id for item in result] == [f'task-{n:05}' for n in range(9900, 9900 + limit)]
    assert storage.reads == {'node': 1, 'page': 1}
    assert storage.returned_edges == limit


@pytest.mark.parametrize('corruption', ['duplicate', 'second_arena', 'parent', 'wrong_source'])
def test_paged_root_validation_preserves_ambiguity_checks(large_arena, corruption):
    storage, mutations = large_arena
    edge = {
        'duplicate': EdgeRecord('Arena', 'gallery', 'CONTAINS_WORK', 'Task', 'task-00000'),
        'second_arena': EdgeRecord('Arena', 'other', 'CONTAINS_WORK', 'Task', 'task-00000'),
        'parent': EdgeRecord('Issue', 'parent', 'HAS_CHILD', 'Task', 'task-00000'),
        'wrong_source': EdgeRecord('Issue', 'parent', 'CONTAINS_WORK', 'Task', 'task-00000'),
    }[corruption]
    storage._edges.append(edge)
    with pytest.raises(ArenaConflict):
        mutations.repository.members('gallery', limit=1)
    with pytest.raises(ArenaConflict):
        mutations.repository.membership('Task', 'task-00000')
    # A malformed earlier page does not conceal or invalidate unrelated roots.
    page = mutations.repository.members('gallery', after_resource='Task/task-00000', limit=1)
    assert len(page) == 1


def test_membership_ignores_unrelated_large_parent_inventory(large_arena):
    storage, mutations = large_arena
    storage._edges.extend([
        EdgeRecord('Issue', 'parent', 'HAS_CHILD', 'Task', 'unrelated')
    ] * 10_001)
    assert ArenaRepository(storage).membership('Task', 'task-00000').arena.id == 'gallery'
    assert not storage.reads['global_inventory']
