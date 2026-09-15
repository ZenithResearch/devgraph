from __future__ import annotations

from dataclasses import dataclass

from devgraph.model.base import WorkObject, WorkStatus


@dataclass(frozen=True)
class Todo(WorkObject):
    pass


@dataclass(frozen=True)
class Proposal(Todo):
    pass


@dataclass(frozen=True)
class Initiative(Todo):
    pass


@dataclass(frozen=True)
class Project(Todo):
    pass


@dataclass(frozen=True)
class Issue(Todo):
    pass


@dataclass(frozen=True)
class Task(Todo):
    pass


@dataclass(frozen=True)
class Requirement(WorkObject):
    pass


@dataclass(frozen=True)
class AcceptanceCriterion(WorkObject):
    pass


@dataclass(frozen=True)
class Blocker(WorkObject):
    pass


@dataclass(frozen=True)
class Decision(WorkObject):
    status: WorkStatus = WorkStatus.ACCEPTED


@dataclass(frozen=True)
class Handoff(WorkObject):
    pass


@dataclass(frozen=True)
class ReviewPacket(WorkObject):
    pass


@dataclass(frozen=True)
class Milestone(WorkObject):
    pass
