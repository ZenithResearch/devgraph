"""Thin HTTP adapters over authorized façade and idempotency seams.

Routes parse and validate transport envelopes, delegate to existing services,
and serialize typed responses. They contain no storage, lifecycle policy,
authorization policy, redaction, or outbox implementation.
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, Header, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse

from devgraph.api.app import ApiServices
from devgraph.api.errors import InvalidVersionPreconditionError, PreconditionRequiredError
from devgraph.api.idempotency import IdempotentWriteExecutor
from devgraph.api.schemas import (
    AcceptProposalRequest,
    ConvertProposalRequest,
    CreateInitiativeObservationRequest,
    CreateWorkObjectRequest,
    ExportByIdsRequest,
    InitiativeObservationEnvelope,
    InitiativeObservationListEnvelope,
    InitiativeObservationMutationResultEnvelope,
    InternalExportResponse,
    MonitorSnapshotEnvelope,
    MutationReceiptEnvelope,
    MutationResultEnvelope,
    PublicSafeSummaryExportResponse,
    RedactedExportResponse,
    StatusTransitionRequest,
    SupportingMaterialEnvelope,
    UpdateWorkObjectRequest,
    WorkDocumentEnvelope,
    WorkKind,
    WorkObjectEnvelope,
    WorkObjectListEnvelope,
    parse_if_match,
)
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.secs_monitor_view_read import (
    DEVGRAPH_MONITOR_ORIGIN_HEADER_V1,
    DEVGRAPH_MONITOR_REQUEST_PROOF_HEADER_V1,
    DEVGRAPH_MONITOR_SESSION_HEADER_V1,
    DEVGRAPH_MONITOR_VIEW_READ_HOST_V1,
    SecSMonitorViewReadDenied,
)
from devgraph.frontend import FRONTEND_HTML
from devgraph.model.base import WorkObject, WorkStatus
from devgraph.model.initiative_observations import (
    InitiativeObservation,
    InitiativeObservationSubjectKind,
)
from devgraph.model.repository import ContentChanges
from devgraph.model.work import Decision, Initiative, Issue, Project, Proposal, Task, Todo

_KIND_CLASSES: dict[str, type[Todo]] = {
    "Proposal": Proposal,
    "Initiative": Initiative,
    "Project": Project,
    "Issue": Issue,
    "Task": Task,
}


def _credential(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    if authorization.startswith("Bearer "):
        return authorization[len("Bearer ") :]
    return authorization


def _raw_path_query(request: Request) -> str:
    raw_path = request.scope.get("raw_path", b"")
    raw_query = request.scope.get("query_string", b"")
    if type(raw_path) is not bytes or type(raw_query) is not bytes:
        raise SecSMonitorViewReadDenied("invalid_monitor_request_target")
    try:
        path = raw_path.decode("ascii", errors="strict")
        query = raw_query.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        raise SecSMonitorViewReadDenied("invalid_monitor_request_target") from None
    return path if not query else f"{path}?{query}"


def _monitor_authority_headers(
    request: Request,
) -> tuple[
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    bool,
    bool,
]:
    relevant = {
        b"authorization": "authorization",
        b"content-length": "content_length",
        b"host": "host",
        b"transfer-encoding": "transfer_encoding",
        DEVGRAPH_MONITOR_ORIGIN_HEADER_V1.lower().encode("ascii"): "origin",
        DEVGRAPH_MONITOR_SESSION_HEADER_V1.lower().encode("ascii"): "session",
        DEVGRAPH_MONITOR_REQUEST_PROOF_HEADER_V1.lower().encode("ascii"): "proof",
    }
    raw_headers = request.scope.get("headers")
    if not isinstance(raw_headers, list):
        raise SecSMonitorViewReadDenied("invalid_monitor_headers")
    selected: dict[str, str | None] = {
        "authorization": None,
        "content_length": None,
        "host": None,
        "origin": None,
        "session": None,
        "proof": None,
        "transfer_encoding": None,
    }
    counts = {name: 0 for name in selected}
    for item in raw_headers:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or type(item[0]) is not bytes
            or type(item[1]) is not bytes
        ):
            raise SecSMonitorViewReadDenied("invalid_monitor_headers")
        name = relevant.get(item[0].lower())
        if name is None:
            continue
        counts[name] += 1
        if counts[name] > 1:
            raise SecSMonitorViewReadDenied("duplicate_monitor_header")
        try:
            selected[name] = item[1].decode("ascii", errors="strict")
        except UnicodeDecodeError:
            raise SecSMonitorViewReadDenied("invalid_monitor_headers") from None
    return (
        selected["authorization"],
        selected["origin"],
        selected["session"],
        selected["proof"],
        selected["host"],
        counts["content_length"] != 0,
        counts["transfer_encoding"] != 0,
    )


async def _empty_monitor_request_body(request: Request) -> bytes:
    async for chunk in request.stream():
        if chunk:
            raise SecSMonitorViewReadDenied("monitor_request_body_not_empty")
    return b""


def _envelope(work: WorkObject) -> WorkObjectEnvelope:
    return WorkObjectEnvelope(
        id=work.id,
        kind=work.kind,
        title=work.title,
        description=work.description,
        status=work.status.value,
        version=work.version,
        priority=work.priority,
        artifact_ids=list(work.artifact_ids),
        external_link_ids=list(work.external_link_ids),
    )


def _observation_envelope(
    observation: InitiativeObservation,
) -> InitiativeObservationEnvelope:
    return InitiativeObservationEnvelope(
        schema_version=observation.schema_version,
        id=observation.id,
        project_id=observation.project_id,
        subject_kind=observation.subject_kind.value,
        subject_url=observation.subject_url,
        github_node_id=observation.github_node_id,
        source_commit=observation.source_commit,
        title=observation.title,
        problem=observation.problem,
        desired_state=observation.desired_state,
        evidence_urls=list(observation.evidence_urls),
        confidence=observation.confidence,
        authorship="inferred",
        claim_status=observation.claim_status.value,
        observed_by=observation.observed_by,
        observation_signature=observation.observation_signature,
        observed_at=observation.observed_at.isoformat(),
    )


def register_routes(app: FastAPI, services: ApiServices) -> None:
    graph = services.authorized_graph

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/monitor", response_class=HTMLResponse, include_in_schema=False)
    def frontend() -> str:
        """Serve the official operator frontend; ``/monitor`` is a stable alias."""
        return FRONTEND_HTML

    @app.get(
        "/monitor/snapshot",
        response_model=MonitorSnapshotEnvelope,
        response_model_exclude_none=True,
    )
    async def monitor_snapshot(
        request: Request,
    ) -> dict:
        (
            authorization,
            monitor_origin,
            signed_session,
            request_proof,
            host,
            has_content_length,
            has_transfer_encoding,
        ) = _monitor_authority_headers(request)
        if any(value is not None for value in (monitor_origin, signed_session, request_proof)):
            if (
                authorization is not None
                or signed_session is None
                or request_proof is None
                or monitor_origin is None
                or host != DEVGRAPH_MONITOR_VIEW_READ_HOST_V1
                or has_content_length
                or has_transfer_encoding
            ):
                raise SecSMonitorViewReadDenied("incomplete_monitor_proof")
            if services.monitor_view_read is None:
                raise UnauthenticatedError("monitor proof verification unavailable")
            return await run_in_threadpool(
                services.monitor_view_read.execute,
                signed_session_header=signed_session,
                request_proof_header=request_proof,
                method=request.method,
                path_query=_raw_path_query(request),
                origin=monitor_origin,
                body=await _empty_monitor_request_body(request),
            )
        return await run_in_threadpool(
            graph.monitor_snapshot,
            _credential(authorization),
        )

    @app.get(
        "/initiative-observations/{observation_id}",
        response_model=InitiativeObservationEnvelope,
    )
    def get_initiative_observation(
        observation_id: str,
        authorization: str | None = Header(default=None),
    ) -> InitiativeObservationEnvelope:
        observation = graph.get_initiative_observation(_credential(authorization), observation_id)
        return _observation_envelope(observation)

    @app.get(
        "/initiative-observations",
        response_model=InitiativeObservationListEnvelope,
    )
    def list_initiative_observations(
        descending: bool = False,
        after_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ) -> InitiativeObservationListEnvelope:
        observations = graph.query_initiative_observations(
            _credential(authorization),
            descending=descending,
            after_id=after_id,
            limit=limit,
        )
        return InitiativeObservationListEnvelope(
            items=[_observation_envelope(observation) for observation in observations]
        )

    @app.get(
        "/work/{kind}/{work_id}/relationships/{relationship}", response_model=WorkObjectListEnvelope
    )
    def related_work(
        kind: WorkKind,
        work_id: str,
        relationship: Literal[
            "children", "parent", "dependencies", "dependents", "blockers", "blocked"
        ],
        after_resource: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ):
        return WorkObjectListEnvelope(
            items=[
                _envelope(item)
                for item in graph.work_relationships(
                    _credential(authorization),
                    kind,
                    work_id,
                    relationship,
                    after_resource=after_resource,
                    limit=limit,
                )
            ]
        )

    @app.get("/work/{kind}/{work_id}/children")
    def children(
        kind: WorkKind,
        work_id: str,
        authorization: str | None = Header(default=None),
    ) -> list[WorkObjectEnvelope]:
        parent = _KIND_CLASSES[kind](id=work_id, title="")
        found = graph.children_of(_credential(authorization), parent)
        return [_envelope(child) for child in found]

    @app.get("/tasks/{task_id}/blockers")
    def blockers(
        task_id: str,
        authorization: str | None = Header(default=None),
    ) -> list[WorkObjectEnvelope]:
        blocked = Task(id=task_id, title="")
        found = graph.blockers_for(_credential(authorization), blocked)
        return [_envelope(task) for task in found]

    @app.get(
        "/work/{kind}/{work_id}/supporting-material", response_model=SupportingMaterialEnvelope
    )
    def supporting_material(
        kind: WorkKind,
        work_id: str,
        response: Response,
        limit: int = Query(default=50, ge=1, le=100),
        after: str | None = Query(default=None, max_length=4096),
        authorization: str | None = Header(default=None),
    ) -> SupportingMaterialEnvelope:
        response.headers["Cache-Control"] = "no-store"
        return graph.supporting_material(
            _credential(authorization), kind, work_id, limit=limit, after=after
        )


    @app.get(
        "/work/{kind}/{work_id}/supporting-material/Artifact/{artifact_id}/document",
        response_model=WorkDocumentEnvelope,
    )
    def work_document(
        kind: WorkKind,
        work_id: str,
        artifact_id: str,
        response: Response,
        authorization: str | None = Header(default=None),
    ) -> WorkDocumentEnvelope:
        response.headers["Cache-Control"] = "no-store"
        return graph.work_document(_credential(authorization), kind, work_id, artifact_id)

    @app.get("/work/{kind}/{work_id}", response_model=WorkObjectEnvelope)
    def get_work(
        kind: WorkKind,
        work_id: str,
        authorization: str | None = Header(default=None),
    ) -> WorkObjectEnvelope:
        return _envelope(graph.get_work_object(_credential(authorization), kind, work_id))

    @app.get("/work/{kind}", response_model=WorkObjectListEnvelope)
    def list_work(
        kind: WorkKind,
        include_archived: bool = False,
        descending: bool = False,
        after_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ) -> WorkObjectListEnvelope:
        found = graph.query_work_objects(
            _credential(authorization),
            kind,
            include_archived=include_archived,
            descending=descending,
            after_id=after_id,
            limit=limit,
        )
        return WorkObjectListEnvelope(items=[_envelope(work) for work in found])

    @app.post("/exports/internal", response_model=InternalExportResponse)
    def export_internal(
        request: ExportByIdsRequest,
        authorization: str | None = Header(default=None),
    ) -> dict:
        return graph.export_internal_by_ids(_credential(authorization), request.kind, request.ids)

    @app.post("/exports/redacted", response_model=RedactedExportResponse)
    def export_redacted(
        request: ExportByIdsRequest,
        authorization: str | None = Header(default=None),
    ) -> dict:
        return graph.export_redacted_by_ids(_credential(authorization), request.kind, request.ids)

    @app.post(
        "/exports/public-safe-summary",
        response_model=PublicSafeSummaryExportResponse,
    )
    def export_public_safe_summary(
        request: ExportByIdsRequest,
        authorization: str | None = Header(default=None),
    ) -> dict:
        return graph.export_public_safe_summary_by_ids(
            _credential(authorization), request.kind, request.ids
        )

    register_write_routes(app, services)


def _receipt_envelope(receipt, *, duplicate: bool) -> MutationReceiptEnvelope:
    return MutationReceiptEnvelope(
        receipt_id=receipt.id,
        operation=receipt.operation,
        subject_label=receipt.subject_label,
        subject_id=receipt.subject_id,
        receipt_status=receipt.status.value,
        duplicate=duplicate,
        correlation_id=receipt.correlation_id,
    )


def register_write_routes(app: FastAPI, services: ApiServices) -> None:
    graph = services.authorized_graph
    executor = IdempotentWriteExecutor(
        authorize_write=graph.authorize_write,
        outbox=services.outbox,
    )

    @app.post(
        "/initiative-observations",
        status_code=201,
        response_model=InitiativeObservationMutationResultEnvelope,
    )
    def create_initiative_observation(
        request: CreateInitiativeObservationRequest,
        response: Response,
        authorization: str | None = Header(default=None),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict:
        credential = _credential(authorization)
        observation = InitiativeObservation(
            id=request.id,
            project_id=request.project_id,
            subject_kind=InitiativeObservationSubjectKind(request.subject_kind),
            subject_url=request.subject_url,
            github_node_id=request.github_node_id,
            source_commit=request.source_commit,
            title=request.title,
            problem=request.problem,
            desired_state=request.desired_state,
            evidence_urls=tuple(request.evidence_urls),
            confidence=request.confidence,
            observed_by=request.observed_by,
            observation_signature=request.observation_signature,
        )
        result, receipt, duplicate = executor.execute(
            credential=credential,
            operation="create_initiative_observation",
            subject_label="Artifact",
            subject_id=observation.id,
            idempotency_key=idempotency_key,
            summary={
                "operation": "create_initiative_observation",
                "schema_version": observation.schema_version,
            },
            mutation=lambda session: session.create_initiative_observation(observation),
        )
        if duplicate:
            response.status_code = 200
        return {
            "observation": (None if result is None else _observation_envelope(result).model_dump()),
            "receipt": _receipt_envelope(receipt, duplicate=duplicate).model_dump(),
        }

    @app.patch("/work/{kind}/{work_id}", response_model=MutationResultEnvelope)
    def update_work(
        kind: WorkKind,
        work_id: str,
        request: UpdateWorkObjectRequest,
        authorization: str | None = Header(default=None),
        idempotency_key: str = Header(alias="Idempotency-Key"),
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> dict:
        if if_match is None:
            raise PreconditionRequiredError
        try:
            expected_version = parse_if_match(if_match)
        except ValueError as error:
            raise InvalidVersionPreconditionError from error
        credential = _credential(authorization)
        changes = ContentChanges.from_mapping(request.model_dump(exclude_unset=True))
        result, receipt, duplicate = executor.execute(
            credential=credential,
            operation="update_work_object_content",
            subject_label=kind,
            subject_id=work_id,
            idempotency_key=idempotency_key,
            summary={"operation": "update_work_object_content"},
            mutation=lambda session: session.update_work_object_content(
                kind,
                work_id,
                expected_version=expected_version,
                changes=changes,
            ),
        )
        return {
            "work": None if result is None else _envelope(result).model_dump(),
            "receipt": _receipt_envelope(receipt, duplicate=duplicate).model_dump(),
        }

    @app.post("/work/{kind}", status_code=201, response_model=MutationResultEnvelope)
    def create_work(
        kind: WorkKind,
        request: CreateWorkObjectRequest,
        response: Response,
        authorization: str | None = Header(default=None),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict:
        credential = _credential(authorization)
        work = _KIND_CLASSES[kind](
            id=request.id,
            title=request.title,
            description=request.description,
            priority=request.priority,
            artifact_ids=tuple(request.artifact_ids),
            external_link_ids=tuple(request.external_link_ids),
        )
        result, receipt, duplicate = executor.execute(
            credential=credential,
            operation="create_work_object",
            subject_label=kind,
            subject_id=request.id,
            idempotency_key=idempotency_key,
            summary={"operation": "create_work_object"},
            mutation=lambda session: session.create_work_object(work),
        )
        if duplicate:
            response.status_code = 200
        return {
            "work": None if result is None else _envelope(result).model_dump(),
            "receipt": _receipt_envelope(receipt, duplicate=duplicate).model_dump(),
        }

    def execute_work_write(
        *, credential, operation, kind, work_id, idempotency_key, mutation
    ) -> dict:
        result, receipt, duplicate = executor.execute(
            credential=credential,
            operation=operation,
            subject_label=kind,
            subject_id=work_id,
            idempotency_key=idempotency_key,
            summary={"operation": operation},
            mutation=mutation,
        )
        return {
            "work": None if result is None else _envelope(result).model_dump(),
            "receipt": _receipt_envelope(receipt, duplicate=duplicate).model_dump(),
        }

    @app.post("/work/{kind}/{work_id}/archive", response_model=MutationResultEnvelope)
    def archive_work(
        kind: WorkKind,
        work_id: str,
        authorization: str | None = Header(default=None),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict:
        credential = _credential(authorization)
        return execute_work_write(
            credential=credential,
            operation="archive_work_object",
            kind=kind,
            work_id=work_id,
            idempotency_key=idempotency_key,
            mutation=lambda session: session.archive_work_object(kind, work_id),
        )

    @app.post("/work/{kind}/{work_id}/status", response_model=MutationResultEnvelope)
    def transition_status(
        kind: WorkKind,
        work_id: str,
        request: StatusTransitionRequest,
        authorization: str | None = Header(default=None),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict:
        credential = _credential(authorization)
        return execute_work_write(
            credential=credential,
            operation="transition_work_object_status",
            kind=kind,
            work_id=work_id,
            idempotency_key=idempotency_key,
            mutation=lambda session: session.transition_work_object_status(
                kind, work_id, WorkStatus(request.status)
            ),
        )

    @app.post("/proposals/{proposal_id}/accept")
    def accept(
        proposal_id: str,
        request: AcceptProposalRequest,
        authorization: str | None = Header(default=None),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict:
        credential = _credential(authorization)
        decision = Decision(id=request.decision_id, title=request.decision_title)
        result, receipt, duplicate = executor.execute(
            credential=credential,
            operation="accept_proposal",
            subject_label="Proposal",
            subject_id=proposal_id,
            idempotency_key=idempotency_key,
            summary={"operation": "accept_proposal", "decision_id": decision.id},
            mutation=lambda session: session.accept_proposal(proposal_id, decision),
        )
        return {
            "work": None if result is None else _envelope(result).model_dump(),
            "receipt": _receipt_envelope(receipt, duplicate=duplicate).model_dump(),
        }

    @app.post("/proposals/{proposal_id}/convert")
    def convert(
        proposal_id: str,
        request: ConvertProposalRequest,
        authorization: str | None = Header(default=None),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict:
        credential = _credential(authorization)
        result, receipt, duplicate = executor.execute(
            credential=credential,
            operation="convert_accepted_proposal_to_issue",
            subject_label="Issue",
            subject_id=request.issue_id,
            idempotency_key=idempotency_key,
            summary={
                "operation": "convert_accepted_proposal_to_issue",
                "proposal_id": proposal_id,
            },
            mutation=lambda session: session.convert_accepted_proposal_to_issue(
                proposal_id, request.issue_id
            ),
        )
        return {
            "work": None if result is None else _envelope(result).model_dump(),
            "receipt": _receipt_envelope(receipt, duplicate=duplicate).model_dump(),
        }
