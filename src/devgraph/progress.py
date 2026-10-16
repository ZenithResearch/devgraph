"""Shared Todo progress semantics. Absence means classification is required, never Backlog."""

from enum import Enum

TODO_KINDS = ("Todo", "Proposal", "Initiative", "Project", "Issue", "Task")


class Progress(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    DONE = "done"


PROGRESS_LABELS = {"not_started": "Not started", "in_progress": "In progress", "done": "Done"}


def stage_progress(workflow_id, stage):
    from devgraph.workflow_contract import STAGES

    if workflow_id not in STAGES or stage not in {row[0] for row in STAGES[workflow_id]}:
        raise ValueError("unknown_workflow_stage")
    return (
        Progress.NOT_STARTED
        if stage == "backlog"
        else (Progress.DONE if stage == "done" else Progress.IN_PROGRESS)
    )


def validate_progress(progress, workflow_json=None):
    if progress is None:
        return None
    value = Progress(progress)
    if workflow_json:
        from devgraph.workflow_contract import decode_state

        state = decode_state(workflow_json)
        if value != stage_progress(state.workflow_id, state.stage):
            raise ValueError("progress_stage_conflict")
    return value


def progress_label(progress):
    return PROGRESS_LABELS.get(progress, "Needs classification")
