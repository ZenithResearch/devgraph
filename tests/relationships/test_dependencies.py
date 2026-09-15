from __future__ import annotations

from devgraph.model.work import Initiative, Issue, Project, Task
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage


def test_task_blocks_edge_is_traversable_in_both_directions():
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)

    blocking_task = Task(id="task-blocking", title="Fix schema migration")
    blocked_task = Task(id="task-blocked", title="Ship relationship traversal")

    graph.add_work_object(blocking_task)
    graph.add_work_object(blocked_task)

    edge = graph.add_blocker(blocking_task, blocked_task)

    assert edge.relationship == "BLOCKS"
    assert edge.from_label == "Task"
    assert edge.from_id == blocking_task.id
    assert edge.to_label == "Task"
    assert edge.to_id == blocked_task.id
    assert graph.blockers_for(blocked_task) == [blocking_task]
    assert graph.blocked_by(blocking_task) == [blocked_task]


def test_blocker_edges_reject_non_task_pairs():
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)

    issue = Issue(id="issue-1", title="Relationships")
    task = Task(id="task-1", title="Implement blockers")

    graph.add_work_object(issue)
    graph.add_work_object(task)

    try:
        graph.add_blocker(task, issue)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "BLOCKS relationships must connect Task to Task" in str(exc)
    else:
        raise AssertionError("BLOCKS should reject non-Task blocked work objects")


def test_depends_on_edge_is_traversable_for_work_objects_in_both_directions():
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)

    dependency = Issue(id="issue-dependency", title="Canonical relationship model")
    dependent = Issue(id="issue-dependent", title="Relationship traversal helpers")

    graph.add_work_object(dependency)
    graph.add_work_object(dependent)

    edge = graph.add_dependency(dependency, dependent)

    assert edge.relationship == "DEPENDS_ON"
    assert edge.from_label == "Issue"
    assert edge.from_id == dependent.id
    assert edge.to_label == "Issue"
    assert edge.to_id == dependency.id
    assert graph.dependencies_of(dependent) == [dependency]
    assert graph.dependents_of(dependency) == [dependent]


def test_depends_on_accepts_different_todo_kinds():
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)

    initiative = Initiative(id="initiative-1", title="Devgraph")
    project = Project(id="project-1", title="Relationships")
    issue = Issue(id="issue-1", title="Blockers and dependencies")
    task = Task(id="task-1", title="Implement helpers")

    for work_object in (initiative, project, issue, task):
        graph.add_work_object(work_object)

    graph.add_dependency(initiative, project)
    graph.add_dependency(project, issue)
    graph.add_dependency(issue, task)

    assert graph.dependencies_of(project) == [initiative]
    assert graph.dependencies_of(issue) == [project]
    assert graph.dependencies_of(task) == [issue]
    assert graph.dependents_of(issue) == [task]
