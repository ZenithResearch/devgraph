"""Canonical workflow execution. Only the signed receiver admits mutations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from devgraph.model.base import utc_now
from devgraph.model.external_links import ExternalLink
from devgraph.model.repository import (
    InvalidStatusTransitionError,
    WorkObjectAlreadyExistsError,
    WorkObjectRepository,
)
from devgraph.model.work import Decision, Handoff, ReviewPacket
from devgraph.progress import Progress, stage_progress
from devgraph.storage.base import StorageUnavailable
from devgraph.workflow_contract import (
    COMPLETION_GATES,
    ENTRY_REVIEWS,
    FORWARD,
    LAYERS,
    RECORD_KINDS,
    RETURNS,
    STAGES,
    WorkflowReview,
    WorkflowState,
    decode_state,
    default_workflow,
    encode_state,
)


class WorkflowConflict(InvalidStatusTransitionError):
    pass


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class Workflows:
    def __init__(self, storage, *, actor_id: str = ""):
        self.storage = storage
        self.repository = WorkObjectRepository(storage)
        self.actor_id = actor_id
        self._edge_cache = {}
        self._node_cache = {}
        self._work_cache = {}
        self._relation_index = {}
        self._basis_cache = {}

    def edges(self, kind):
        if kind not in self._edge_cache:
            edges = self.storage.list_edges(kind, limit=10001)
            if len(edges) > 10000:
                raise StorageUnavailable("workflow relationship budget exceeded")
            self._edge_cache[kind] = edges
        return self._edge_cache[kind]

    def node(self, kind, identifier):
        key = (kind, identifier)
        if key not in self._node_cache:
            self._node_cache[key] = self.storage.get_node(kind, identifier)
        return self._node_cache[key]

    def related(self, relation, work, *, incoming=False):
        key = (relation, incoming)
        if key not in self._relation_index:
            index = {}
            for edge in self.edges(relation):
                source = (
                    (edge.to_label, edge.to_id) if incoming else (edge.from_label, edge.from_id)
                )
                index.setdefault(source, []).append(edge)
            self._relation_index[key] = index
        return self._relation_index[key].get((work.kind, work.id), [])

    def work(self, kind, identifier):
        key = (kind, identifier)
        if key not in self._work_cache:
            self._work_cache[key] = self.repository.get_by_id(kind, identifier)
        return self._work_cache[key]

    def children(self, work):
        return [self.work(e.to_label, e.to_id) for e in self.related("HAS_CHILD", work)]

    def requirements(self, work):
        # Criteria can attach directly to Todo work or to one of its requirements.
        result = {
            (e.to_label, e.to_id)
            for e in self.related("HAS_REQUIREMENT", work)
            + self.related("HAS_ACCEPTANCE_CRITERION", work)
        }
        for kind, identifier in list(result):
            if kind == "Requirement":
                requirement = self.work(kind, identifier)
                result.update(
                    (e.to_label, e.to_id)
                    for e in self.related("HAS_ACCEPTANCE_CRITERION", requirement)
                )
        return sorted(result)

    def basis(self, work, phase="requirements"):
        planning = phase in {"developer_plan", "ceo_plan", "baseline"}
        key = (work.kind, work.id, work.version, planning)
        if key not in self._basis_cache:
            fields = ("title", "description", "artifact_ids", "external_link_ids")
            content = {name: getattr(work, name) for name in fields}
            children = [] if planning else [(c.kind, c.id, c.version) for c in self.children(work)]
            requirements = []
            for kind, identifier in self.requirements(work):
                node = self.node(kind, identifier)
                requirements.append(
                    (kind, identifier, None if node is None else (node.archived, node.properties))
                )
            self._basis_cache[key] = fingerprint([content, sorted(children), requirements])
        return self._basis_cache[key]

    def review(self, work, state, phase):
        record_id = state.reviews.get(phase)
        if record_id is None:
            return None
        node = self.node(RECORD_KINDS[phase], record_id)
        if node is None or node.archived:
            return None
        try:
            record = json.loads(node.properties["description"])
            if (
                record["schema"] != "devgraph.workflow-review.v1"
                or record["subject"] != f"{work.kind}/{work.id}"
                or record["basis"] != self.basis(work, phase)
                or record["review"]["phase"] != phase
            ):
                return None
            for ref, digest in record["evidence_hashes"].items():
                kind, identifier = ref.split("/", 1)
                evidence = self.node(kind, identifier)
                if (
                    evidence is None
                    or evidence.archived
                    or fingerprint(evidence.properties) != digest
                ):
                    return None
            return WorkflowReview.model_validate(record["review"], strict=True)
        except (KeyError, ValueError, TypeError):
            return None

    def _approved(self, work, state, phase, *, evidence=False):
        record = self.review(work, state, phase)
        if record is None or record.verdict != "approved" or (evidence and not record.evidence):
            return False
        if phase == "technical":
            return {x.layer for x in record.layers} == set(LAYERS) and all(
                x.verdict != "changes_requested" for x in record.layers
            )
        return True

    def completion_gaps(self, work, state):
        gaps = []
        ledger = self.review(work, state, "requirements")
        if (
            ledger is None
            or ledger.verdict != "approved"
            or not ledger.requirements
            or any(x.outcome == "unmet" for x in ledger.requirements)
        ):
            gaps.append(
                "Record satisfied requirements and their evidence, or explained exclusions."
            )
        expected = {f"{kind}/{identifier}" for kind, identifier in self.requirements(work)}
        if ledger is not None and not expected <= {r.subject for r in ledger.requirements}:
            gaps.append(
                "Include every attached requirement and acceptance criterion in the review ledger."
            )
        exclusions = (
            {r.subject for r in ledger.requirements if r.outcome == "excluded"} if ledger else set()
        )
        for child in self.children(work):
            if child.progress != Progress.DONE and f"{child.kind}/{child.id}" not in exclusions:
                gaps.append("Complete child work or record its explicit scope exclusion.")
                break
        for edge in self.related("BLOCKS", work, incoming=True):
            blocker = self.work(edge.from_label, edge.from_id)
            if blocker.progress != Progress.DONE:
                gaps.append("Resolve blocking work before completing this item.")
                break
        return gaps

    def gaps(self, work, state, target):
        gaps = []
        if work.archived:
            return ["Archived work cannot move."]
        if target == "blocked_waiting_input":
            return []
        if state.stage == "blocked_waiting_input":
            if not self._approved(work, state, "resolution", evidence=True):
                gaps.append("Record the required input or decision before resuming.")
            return gaps
        back = RETURNS[state.workflow_id].get(state.stage)
        if back == target:
            return []
        phases = list(ENTRY_REVIEWS.get(target, ()))
        if target == "done":
            phases += (
                ["developer_plan", "ceo_plan", "ceo_delivery"]
                if state.workflow_id == "vibe-ceo.v1"
                else ["baseline", "commit"]
            )
        for phase in phases:
            if not self._approved(work, state, phase, evidence=True):
                gaps.append(f"Record current {phase.replace('_', ' ')} approval and evidence.")
        if target in COMPLETION_GATES:
            gaps.extend(self.completion_gaps(work, state))
        if target == "done" and work.kind == "Proposal" and self.disposition(work) is None:
            gaps.append("Record the proposal disposition with valid Decision provenance.")
        return list(dict.fromkeys(gaps))

    def disposition(self, work):
        found = []
        for relation, value in (
            ("ACCEPTED_BY_DECISION", "accepted"),
            ("REJECTED_BY_DECISION", "rejected"),
        ):
            for edge in self.related(relation, work):
                node = self.node("Decision", edge.to_id) if edge.to_label == "Decision" else None
                if node is None or node.archived:
                    return None
                try:
                    self.repository._from_node(node)
                except (ValueError, TypeError):
                    return None
                found.append(value)
        return found[0] if len(found) == 1 else None

    def transitions(self, work):
        if not work.workflow_json:
            return []
        state = decode_state(work.workflow_json)
        if state.stage == "done" or (work.archived):
            return []
        if state.stage == "blocked_waiting_input":
            targets = [state.resume_stage]
        else:
            targets = [FORWARD[state.workflow_id][state.stage]]
            if state.stage in RETURNS[state.workflow_id]:
                targets.append(RETURNS[state.workflow_id][state.stage])
            targets.append("blocked_waiting_input")
        rows = {s: (label, column) for s, label, column in STAGES[state.workflow_id]}
        return [
            {
                "stage": t,
                "label": rows[t][0],
                "column": rows[t][1],
                "unmet_requirements": self.gaps(work, state, t),
            }
            for t in targets
        ]

    def _save(self, work, state):
        updated = replace(
            work,
            workflow_json=encode_state(state),
            progress=stage_progress(state.workflow_id, state.stage),
            updated_at=utc_now(),
            version=work.version + 1,
        )
        self.storage.update_node(work.kind, work.id, updated.to_node_properties())
        return updated

    def execute(self, work, operation, payload):
        if work.archived:
            raise WorkflowConflict("Archived work cannot change workflow.")
        if work.kind == "Todo":
            raise WorkflowConflict("Base Todos use shared progress without a detailed workflow.")
        if operation == "workflow.assign":
            if work.workflow_json is not None:
                raise WorkflowConflict("Workflow is already assigned; moves do not reassign it.")
            # Classifying legacy work must not manufacture approvals for skipped gates.
            if payload["stage"] not in {"backlog", "planning", "intake"}:
                raise WorkflowConflict(
                    "Classify into Backlog or Planning, then record the required gates."
                )
            if (
                work.progress in (Progress.IN_PROGRESS, Progress.DONE)
                and payload["stage"] == "backlog"
            ):
                raise WorkflowConflict("Started work cannot be classified as Not started.")
            state = WorkflowState(workflow_id=payload["workflow_id"], stage=payload["stage"])
            return self._save(work, state)
        if work.workflow_json is None:
            raise WorkflowConflict("Classify the workflow before recording reviews or moving work.")
        state = decode_state(work.workflow_json)
        if state.stage == "done":
            raise WorkflowConflict("Completed work requires a new follow-up item.")
        if operation == "workflow.review":
            return self._record(work, state, WorkflowReview.model_validate(payload, strict=True))
        target = payload["stage"]
        legal = {t["stage"]: t for t in self.transitions(work)}
        if target not in legal:
            raise WorkflowConflict("This transition would bypass the workflow.")
        if legal[target]["unmet_requirements"]:
            raise WorkflowConflict(" ".join(legal[target]["unmet_requirements"]))
        if target == "blocked_waiting_input":
            if not payload["reason"].strip():
                raise WorkflowConflict("Explain what input or decision is needed.")
            state.resume_stage, state.blocking_reason = state.stage, payload["reason"]
            state.reviews.pop("resolution", None)
        elif state.stage == "blocked_waiting_input":
            state.resume_stage, state.blocking_reason = None, ""
        elif RETURNS[state.workflow_id].get(state.stage) == target:
            if not payload["reason"].strip():
                raise WorkflowConflict("Record feedback before returning work.")
            invalidate = (
                list(state.reviews)
                if target in {"planning", "intake"}
                else ["commit", "technical", "ceo_delivery", "handoff", "requirements"]
            )
            for phase in invalidate:
                state.reviews.pop(phase, None)
        state.stage = target
        return self._save(work, state)

    def _record(self, work, state, review):
        required_stage = {
            "developer_plan": {"developer_plan_approval"},
            "ceo_plan": {"ceo_plan_approval"},
            "ceo_delivery": {"ceo_delivery_approval"},
            "baseline": {"requirements_normalized"},
            "commit": {"implementing_commit"},
            "technical": {"technical_review", "layer_review"},
            "handoff": {"handoff_verification", "delivery_packet_ready"},
            "resolution": {"blocked_waiting_input"},
        }
        if review.phase in required_stage and state.stage not in required_stage[review.phase]:
            raise WorkflowConflict("Record this approval at its own workflow gate.")
        if review.phase == "requirements" and not review.requirements:
            raise WorkflowConflict("A requirements review needs its complete requirement ledger.")
        label = RECORD_KINDS[review.phase]
        if self.storage.get_node(label, review.record_id) is not None:
            raise WorkObjectAlreadyExistsError("Review record already exists.")
        hashes = {}
        for evidence in review.evidence:
            node = self.storage.get_node(evidence.kind, evidence.id)
            if evidence.url:
                if node is not None:
                    raise WorkObjectAlreadyExistsError("Evidence link already exists.")
                link = ExternalLink(id=evidence.id, title=evidence.title, url=evidence.url)
                node = self.storage.create_node(link.kind, link.id, link.to_node_properties())
            if node is None or node.archived:
                raise WorkflowConflict(
                    "Evidence must resolve to an active Artifact or ExternalLink."
                )
            hashes[f"{evidence.kind}/{evidence.id}"] = fingerprint(node.properties)
        body = {
            "schema": "devgraph.workflow-review.v1",
            "subject": f"{work.kind}/{work.id}",
            "basis": self.basis(work, review.phase),
            "actor_id": self.actor_id,
            "evidence_hashes": hashes,
            "review": review.model_dump(),
        }
        model = {"Decision": Decision, "Handoff": Handoff, "ReviewPacket": ReviewPacket}[label]
        record = model(
            id=review.record_id,
            title=review.summary[:200],
            description=json.dumps(body, sort_keys=True, separators=(",", ":")),
        )
        self.repository.create(record)
        relation = {
            "Decision": "HAS_WORKFLOW_DECISION",
            "Handoff": "HAS_HANDOFF",
            "ReviewPacket": "HAS_REVIEW_PACKET",
        }[label]
        self.storage.create_edge(work.kind, work.id, relation, label, record.id)
        for evidence in review.evidence:
            self.storage.create_edge(
                label,
                record.id,
                "HAS_ARTIFACT" if evidence.kind == "Artifact" else "HAS_EXTERNAL_LINK",
                evidence.kind,
                evidence.id,
            )
        state.reviews[review.phase] = record.id
        return self._save(work, state)

    def detail(self, work):
        state = decode_state(work.workflow_json) if work.workflow_json else None
        children = self.children(work)
        done = sum(c.progress == Progress.DONE for c in children)
        requirements = self.requirements(work)
        unfinished = [c for c in children if c.progress != Progress.DONE]
        return {
            "schema": "devgraph.work-workflow.v1",
            "kind": work.kind,
            "id": work.id,
            "version": str(work.version),
            "progress": work.progress,
            "archived": work.archived,
            "workflow": state.model_dump() if state else None,
            "default_workflow": None if work.kind == "Todo" else default_workflow(work.kind),
            "transitions": self.transitions(work),
            "child_progress": {"done": done, "total": len(children)},
            "completion_gaps": self.completion_gaps(work, state)
            if state
            else ([] if work.kind == "Todo" else ["Stage not set"]),
            "requirement_subjects": [
                {
                    "subject": f"{kind}/{identifier}",
                    "id": "scope-" + fingerprint([kind, identifier]),
                    "title": (
                        self.node(kind, identifier).properties.get("title", identifier)
                        if self.node(kind, identifier)
                        else identifier
                    ),
                }
                for kind, identifier in requirements[:100]
            ],
            "requirement_count": len(requirements),
            "child_subjects": [
                {
                    "subject": f"{c.kind}/{c.id}",
                    "id": "scope-" + fingerprint([c.kind, c.id]),
                    "title": c.title[:240],
                }
                for c in unfinished[:100]
            ],
            "unfinished_child_count": len(unfinished),
            "current_reviews": {
                phase: record.model_dump() if record else None
                for phase in state.reviews
                for record in [self.review(work, state, phase)]
            }
            if state
            else {},
        }
