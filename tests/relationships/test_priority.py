from __future__ import annotations

from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.work import Issue, Proposal, Task
from devgraph.relationships import RelationshipGraph
from devgraph.services.priority import EffectivePriorityService
from devgraph.storage.memory import MemoryGraphStorage


def _graph_and_service() -> tuple[RelationshipGraph, EffectivePriorityService]:
    graph = RelationshipGraph(MemoryGraphStorage())
    return graph, EffectivePriorityService(graph)


def test_default_base_priority_is_zero_and_serialized_in_node_properties():
    task = Task(id="task-1", title="Default priority")

    assert task.priority == 0
    assert task.to_node_properties()["priority"] == 0


def test_task_without_blocks_edges_returns_base_priority():
    graph, service = _graph_and_service()
    task = Task(id="task-1", title="Standalone task", priority=3)
    graph.add_work_object(task)

    assert service.effective_priority(task) == 3


def test_non_task_todo_returns_base_priority():
    graph, service = _graph_and_service()
    issue = Issue(id="issue-1", title="Issue outside BLOCKS scope", priority=2)
    graph.add_work_object(issue)

    assert service.effective_priority(issue) == 2


def test_blocking_task_is_escalated_to_max_priority_of_blocked_tasks():
    graph, service = _graph_and_service()
    blocking = Task(id="task-blocking", title="Low-priority blocker", priority=0)
    blocked_high = Task(id="task-blocked-high", title="Urgent blocked work", priority=5)
    blocked_low = Task(id="task-blocked-low", title="Less urgent blocked work", priority=3)
    for task in (blocking, blocked_high, blocked_low):
        graph.add_work_object(task)
    graph.add_blocker(blocking, blocked_high)
    graph.add_blocker(blocking, blocked_low)

    assert service.effective_priority(blocking) == 5
    assert service.effective_priority(blocked_high) == 5
    assert service.effective_priority(blocked_low) == 3


def test_effective_priority_escalates_transitively_through_blocks_chain():
    graph, service = _graph_and_service()
    task_a = Task(id="task-a", title="Root blocker", priority=0)
    task_b = Task(id="task-b", title="Middle blocker", priority=1)
    task_c = Task(id="task-c", title="Urgent downstream work", priority=7)
    for task in (task_a, task_b, task_c):
        graph.add_work_object(task)
    graph.add_blocker(task_a, task_b)
    graph.add_blocker(task_b, task_c)

    assert service.effective_priority(task_a) == 7
    assert service.effective_priority(task_b) == 7
    assert service.effective_priority(task_c) == 7


def test_blocking_task_with_higher_own_priority_keeps_it():
    graph, service = _graph_and_service()
    blocking = Task(id="task-blocking", title="Already urgent blocker", priority=9)
    blocked = Task(id="task-blocked", title="Lower-priority blocked work", priority=2)
    graph.add_work_object(blocking)
    graph.add_work_object(blocked)
    graph.add_blocker(blocking, blocked)

    assert service.effective_priority(blocking) == 9


def test_blocks_cycle_terminates_and_returns_max_of_base_priorities():
    graph, service = _graph_and_service()
    task_a = Task(id="task-a", title="Cycle member A", priority=1)
    task_b = Task(id="task-b", title="Cycle member B", priority=5)
    graph.add_work_object(task_a)
    graph.add_work_object(task_b)
    graph.add_blocker(task_a, task_b)
    graph.add_blocker(task_b, task_a)

    assert service.effective_priority(task_a) == 5
    assert service.effective_priority(task_b) == 5


def test_priority_survives_relationship_graph_round_trip():
    graph, _service = _graph_and_service()
    blocking = Task(id="task-blocking", title="Blocker", priority=4)
    blocked = Task(id="task-blocked", title="Blocked", priority=6)
    graph.add_work_object(blocking)
    graph.add_work_object(blocked)
    graph.add_blocker(blocking, blocked)

    assert graph.blockers_for(blocked) == [blocking]
    assert graph.blockers_for(blocked)[0].priority == 4
    assert graph.blocked_by(blocking) == [blocked]
    assert graph.blocked_by(blocking)[0].priority == 6


def test_priority_survives_proposal_lifecycle_round_trip():
    lifecycle = ProposalLifecycle(MemoryGraphStorage())
    proposal = Proposal(id="proposal-1", title="Priority-bearing proposal", priority=8)
    lifecycle.create_proposal(proposal)

    assert lifecycle.get_proposal("proposal-1").priority == 8
