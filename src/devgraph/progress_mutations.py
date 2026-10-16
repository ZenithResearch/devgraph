"""Versioned, attested progress changes inside the named mutation transaction."""

import json
from dataclasses import replace

from devgraph.model.base import utc_now
from devgraph.model.external_links import ExternalLink
from devgraph.model.repository import WorkObjectAlreadyExistsError, WorkObjectRepository
from devgraph.model.work import Decision, ReviewPacket
from devgraph.progress import Progress, stage_progress
from devgraph.progress_contract import ProgressSet
from devgraph.workflow_contract import WorkflowState, decode_state, encode_state
from devgraph.workflows import WorkflowConflict, Workflows, fingerprint


def set_progress(storage, work, payload, actor_id):
    change = ProgressSet.model_validate(payload, strict=True)
    target = Progress(change.progress)
    engine = Workflows(storage, actor_id=actor_id)
    state = decode_state(work.workflow_json) if work.workflow_json else None
    if work.archived:
        raise WorkflowConflict("Restore the item before changing progress.")
    if target == work.progress:
        raise WorkflowConflict("Progress is already set to that value.")
    if target == Progress.NOT_STARTED and work.progress is not None:
        raise WorkflowConflict("Rework remains In progress; it cannot return to Not started.")
    workflow_json = work.workflow_json
    rejected = work.kind == "Proposal" and engine.disposition(work) == "rejected"
    if rejected and target == Progress.DONE:
        if not change.evidence:
            raise WorkflowConflict("Record evidence that the proposal disposition was handed off.")
        if state:
            state.stage = "done"
            state.resume_stage = None
            state.blocking_reason = ""
            workflow_json = encode_state(state)
    elif state:
        mapped = stage_progress(state.workflow_id, state.stage)
        if work.progress == Progress.DONE and target == Progress.IN_PROGRESS:
            state = WorkflowState(
                workflow_id=state.workflow_id,
                stage="planning" if state.workflow_id == "vibe-ceo.v1" else "intake",
            )
            workflow_json = encode_state(state)
        elif target != mapped:
            raise WorkflowConflict(
                "Use a legal workflow transition to change this subtype's progress."
            )
        if target == Progress.DONE:
            gaps = engine.gaps(work, state, "done")
            if gaps:
                raise WorkflowConflict(" ".join(gaps))
    elif target == Progress.DONE and work.kind != "Todo":
        raise WorkflowConflict(
            "Complete this subtype's applicable workflow and approval gates first."
        )
    if target == Progress.DONE:
        expected = {f"{k}/{i}" for k, i in engine.requirements(work)}
        ledger = {row.subject: row for row in change.requirements}
        if work.kind == "Todo" and (
            not expected <= set(ledger)
            or any(row.outcome == "unmet" for row in change.requirements)
        ):
            raise WorkflowConflict(
                "Satisfy every attached requirement with evidence or a reasoned exclusion."
            )
        for edge in engine.related("BLOCKS", work, incoming=True):
            if engine.work(edge.from_label, edge.from_id).progress != Progress.DONE:
                raise WorkflowConflict("Resolve blocking work before completion.")
    if storage.get_node("ReviewPacket", change.record_id) is not None:
        raise WorkObjectAlreadyExistsError("Progress record already exists.")
    hashes = {}
    for evidence in change.evidence:
        node = storage.get_node(evidence.kind, evidence.id)
        if evidence.url:
            if node is not None:
                raise WorkObjectAlreadyExistsError("Evidence link already exists.")
            link = ExternalLink(id=evidence.id, title=evidence.title, url=evidence.url)
            node = storage.create_node(link.kind, link.id, link.to_node_properties())
        if node is None or node.archived:
            raise WorkflowConflict("Evidence must resolve to an active Artifact or ExternalLink.")
        hashes[f"{evidence.kind}/{evidence.id}"] = fingerprint(node.properties)
    record = ReviewPacket(
        id=change.record_id,
        title=change.reason[:200],
        description=json.dumps(
            {
                "schema": "devgraph.progress-record.v1",
                "subject": f"{work.kind}/{work.id}",
                "expected_version": work.version,
                "basis": engine.basis(work),
                "actor_id": actor_id,
                "from_progress": work.progress,
                "to_progress": target,
                "evidence_hashes": hashes,
                "attestation": change.model_dump(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    WorkObjectRepository(storage).create(record)
    storage.create_edge(work.kind, work.id, "HAS_REVIEW_PACKET", "ReviewPacket", record.id)
    for evidence in change.evidence:
        storage.create_edge(
            "ReviewPacket",
            record.id,
            "HAS_ARTIFACT" if evidence.kind == "Artifact" else "HAS_EXTERNAL_LINK",
            evidence.kind,
            evidence.id,
        )
    updated = replace(
        work,
        progress=target,
        workflow_json=workflow_json,
        progress_record_id=record.id,
        version=work.version + 1,
        updated_at=utc_now(),
    )
    storage.update_node(work.kind, work.id, updated.to_node_properties())
    return updated


def reject_proposal(storage, work, payload):
    if work.kind != "Proposal" or work.archived:
        raise WorkflowConflict("Only an active Proposal can be rejected.")
    engine = Workflows(storage)
    if engine.related("ACCEPTED_BY_DECISION", work) or engine.related("REJECTED_BY_DECISION", work):
        raise WorkflowConflict("The proposal already has a recorded disposition.")
    decision = Decision(
        id=payload["decision_id"], title=payload["reason"][:200], description=payload["reason"]
    )
    WorkObjectRepository(storage).create(decision)
    storage.create_edge("Proposal", work.id, "REJECTED_BY_DECISION", "Decision", decision.id)
    return WorkObjectRepository(storage).update(work, expected_version=work.version)


def completion_record_gaps(engine, work):
    """Validate an existing simple-Todo or rejected-disposition attestation on migration."""
    try:
        node = engine.node("ReviewPacket", work.progress_record_id)
        if node is None or node.archived:
            raise ValueError()
        record = json.loads(node.properties["description"])
        change = ProgressSet.model_validate(record["attestation"], strict=True)
        evidence_keys = {f"{e.kind}/{e.id}" for e in change.evidence}
        if (
            record["schema"] != "devgraph.progress-record.v1"
            or record["subject"] != f"{work.kind}/{work.id}"
            or record["basis"] != engine.basis(work)
            or record["to_progress"] != "done"
            or change.progress != "done"
            or not record["actor_id"]
            or set(record["evidence_hashes"]) != evidence_keys
        ):
            raise ValueError()
        for ref, digest in record["evidence_hashes"].items():
            kind, identifier = ref.split("/", 1)
            evidence = engine.node(kind, identifier)
            if evidence is None or evidence.archived or fingerprint(evidence.properties) != digest:
                raise ValueError()
        if work.kind == "Proposal" and (
            engine.disposition(work) != "rejected" or not change.evidence
        ):
            raise ValueError()
        expected = {f"{k}/{i}" for k, i in engine.requirements(work)}
        if work.kind == "Todo" and (
            not expected <= {row.subject for row in change.requirements}
            or any(row.outcome == "unmet" for row in change.requirements)
        ):
            raise ValueError()
        for edge in engine.related("BLOCKS", work, incoming=True):
            if engine.work(edge.from_label, edge.from_id).progress != Progress.DONE:
                raise ValueError()
        return []
    except (ValueError, KeyError, TypeError):
        return ["Completion attestation is missing, stale, or has unsatisfied requirements."]
