from __future__ import annotations

from devgraph.model.work import Initiative, Issue, Project, Task
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage


def test_parentage_edges_create_traversable_initiative_project_issue_task_chain():
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)

    initiative = Initiative(id="initiative-1", title="Devgraph")
    project = Project(id="project-1", title="Issue train")
    issue = Issue(id="issue-1", title="Relationships")
    task = Task(id="task-1", title="Parentage")

    graph.add_work_object(initiative)
    graph.add_work_object(project)
    graph.add_work_object(issue)
    graph.add_work_object(task)

    graph.add_parent(project, initiative)
    graph.add_parent(issue, project)
    graph.add_parent(task, issue)

    assert graph.children_of(initiative) == [project]
    assert graph.children_of(project) == [issue]
    assert graph.children_of(issue) == [task]
    assert graph.parent_of(task) == issue
    assert graph.ancestor_chain(task) == [issue, project, initiative]


def test_parentage_edges_reject_invalid_parent_child_pairs():
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)
    initiative = Initiative(id="initiative-1", title="Devgraph")
    issue = Issue(id="issue-1", title="Relationships")

    graph.add_work_object(initiative)
    graph.add_work_object(issue)

    try:
        graph.add_parent(issue, initiative)
    except ValueError as exc:
        assert "invalid parentage" in str(exc)
    else:
        raise AssertionError("Issue cannot be parented directly by Initiative in v0")
