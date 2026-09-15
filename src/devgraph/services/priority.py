from __future__ import annotations

from devgraph.model.base import WorkObject
from devgraph.model.work import Task
from devgraph.relationships import RelationshipGraph


class EffectivePriorityService:
    """Read-only effective-priority computation over BLOCKS edges.

    A blocking Task inherits the maximum effective priority of the work it
    blocks, transitively across BLOCKS edges (classic priority inheritance):

        effective_priority(x) = max(x.priority, effective_priority of every
        work object x BLOCKS)

    BLOCKS edges connect Task to Task in v0, so non-Task work objects simply
    report their base priority. Traversal is cycle-safe: already-visited
    tasks contribute their base priority instead of being re-expanded.
    """

    def __init__(self, graph: RelationshipGraph) -> None:
        self._graph = graph

    def effective_priority(self, work_object: WorkObject) -> int:
        if not isinstance(work_object, Task):
            return work_object.priority
        return self._effective_priority(work_object, set())

    def _effective_priority(self, task: Task, visited: set[str]) -> int:
        if task.id in visited:
            return task.priority
        visited.add(task.id)
        priority = task.priority
        for blocked in self._graph.blocked_by(task):
            priority = max(priority, self._effective_priority(blocked, visited))
        return priority
