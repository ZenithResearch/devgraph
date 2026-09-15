"""Bounded synchronous HTTP client for the canonical Devgraph Work API."""

from __future__ import annotations

import base64
import json as json_codec
import math
import re
from collections.abc import Sequence
from numbers import Real
from typing import Any, Literal, Protocol, TypeAlias, cast
from urllib.parse import quote, urlencode

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    field_validator,
    model_validator,
)

from devgraph.arena_contract import Arena, ArenaList, ArenaMembership
from devgraph.model.validation import validate_page_limit, validate_work_object_id
from devgraph.supporting_material_contract import SupportingMaterialEnvelope, WorkDocumentEnvelope

WorkKind: TypeAlias = Literal["Proposal", "Initiative", "Project", "Issue", "Task"]
WorkStatus: TypeAlias = Literal["draft", "review", "accepted", "archived"]
MutationOperation: TypeAlias = Literal[
    "create_work_object",
    "update_work_object_content",
    "transition_work_object_status",
    "archive_work_object",
    "accept_proposal",
    "convert_accepted_proposal_to_issue",
    "devgraph.work.create.v1",
    "devgraph.work.patch.v1",
    "devgraph.work.status.v1",
    "devgraph.work.archive.v1",
    "devgraph.work.accept.v1",
    "devgraph.work.convert.v1",
    "devgraph.work.parent.set.v1",
    "devgraph.work.dependency.add.v1",
    "devgraph.work.dependency.remove.v1",
    "devgraph.work.blocker.add.v1",
    "devgraph.work.blocker.remove.v1",
]
ReceiptStatus: TypeAlias = Literal["pending", "dispatched_dry_run", "retry_scheduled", "failed"]

_WORK_KINDS: frozenset[str] = frozenset({"Proposal", "Initiative", "Project", "Issue", "Task"})
_WORK_STATUSES: frozenset[str] = frozenset({"draft", "review", "accepted", "archived"})


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
    )


class DevgraphRequestContext(_StrictFrozenModel):
    credential: str = Field(min_length=1, repr=False, exclude=True)


class DevgraphWorkContext(_StrictFrozenModel):
    """Transport a secS projection; this client cannot issue authority."""

    projection_json: bytes = Field(min_length=1, max_length=16_384, repr=False, exclude=True)


class WorkObject(_StrictFrozenModel):
    id: str
    kind: WorkKind
    title: str
    description: str
    status: WorkStatus
    version: int = Field(ge=1)
    priority: int
    artifact_ids: tuple[str, ...]
    external_link_ids: tuple[str, ...]

    @field_validator("artifact_ids", "external_link_ids", mode="before")
    @classmethod
    def freeze_string_lists(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class MutationReceipt(_StrictFrozenModel):
    receipt_id: str
    operation: MutationOperation
    subject_label: WorkKind
    subject_id: str
    receipt_status: ReceiptStatus
    duplicate: bool
    correlation_id: str


class MutationResult(_StrictFrozenModel):
    work: WorkObject | None
    receipt: MutationReceipt

    @model_validator(mode="after")
    def duplicate_shape_is_consistent(self) -> MutationResult:
        if self.receipt.duplicate == (self.work is not None):
            raise ValueError("mutation result duplicate shape is inconsistent")
        return self


class ArenaMutationReceipt(_StrictFrozenModel):
    receipt_id: str
    operation: Literal["devgraph.arena.create.v1", "devgraph.arena.patch.v1",
                       "devgraph.arena.archive.v1", "devgraph.arena.member.set.v1"]
    subject_label: Literal["Arena", "Initiative", "Task"]
    subject_id: str
    receipt_status: ReceiptStatus
    duplicate: bool
    correlation_id: str


class ArenaMutationResult(_StrictFrozenModel):
    arena: Arena | None
    work: WorkObject | None
    receipt: ArenaMutationReceipt

    @model_validator(mode="after")
    def consistent_result(self):
        member = self.receipt.operation == "devgraph.arena.member.set.v1"
        result = self.work if member else self.arena
        if (self.arena is not None if member else self.work is not None) or (
            self.receipt.duplicate == (result is not None)
        ):
            raise ValueError("inconsistent Arena mutation result")
        return self


class WorkObjectList(_StrictFrozenModel):
    items: tuple[WorkObject, ...]

    @field_validator("items", mode="before")
    @classmethod
    def freeze_items(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class WorkObjectCollection(RootModel[tuple[WorkObject, ...]]):
    """Strict immutable projection of raw children/blocker arrays."""

    model_config = ConfigDict(frozen=True, strict=True, hide_input_in_errors=True)

    @model_validator(mode="before")
    @classmethod
    def freeze_root(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @property
    def items(self) -> tuple[WorkObject, ...]:
        return self.root


class _ProblemBody(_StrictFrozenModel):
    status: int
    title: str
    type: str
    detail: str = ""
    correlation_id: str | None = None


class DevgraphClientError(Exception):
    """Safe base error that never retains request or raw-response material."""


class DevgraphProblem(DevgraphClientError):
    def __init__(
        self,
        *,
        status: int,
        title: str,
        type: str,
        detail: str = "",
        correlation_id: str | None = None,
    ) -> None:
        self.status = status
        self.title = title
        self.type = type
        self.detail = detail
        self.correlation_id = correlation_id
        super().__init__(f"Devgraph problem {status}: {title}")

    def __repr__(self) -> str:
        return f"DevgraphProblem(status={self.status!r}, title={self.title!r})"


class DevgraphMalformedSuccess(DevgraphClientError):
    def __init__(self) -> None:
        super().__init__("Devgraph success response was malformed")


class DevgraphInvalidSuccessEnvelope(DevgraphClientError):
    def __init__(self) -> None:
        super().__init__("Devgraph success response envelope was invalid")


class DevgraphUnexpectedContentType(DevgraphClientError):
    def __init__(self) -> None:
        super().__init__("Devgraph success response had an unexpected content type")


class DevgraphMalformedProblem(DevgraphClientError):
    def __init__(self) -> None:
        super().__init__("Devgraph problem response was malformed")


class DevgraphTimeout(DevgraphClientError):
    def __init__(self) -> None:
        super().__init__("Devgraph request timed out")


class DevgraphTransportError(DevgraphClientError):
    def __init__(self) -> None:
        super().__init__("Devgraph transport failed")


class SyncResponse(Protocol):
    status_code: int
    headers: Any

    def json(self) -> Any: ...


class SyncRequestTransport(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> SyncResponse: ...


class DevgraphHttpClient:
    """HTTP-only client for the bounded canonical Work operations."""

    def __init__(
        self,
        *,
        transport: SyncRequestTransport,
        base_url: str,
        timeout: float,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must not be empty")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, Real)
            or not math.isfinite(float(timeout))
            or timeout <= 0
        ):
            raise ValueError("timeout must be positive")
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def query_cypher(
        self, context: DevgraphRequestContext, *, request_json: bytes,
    ) -> dict[str, Any]:
        from devgraph.cypher_read import (
            MAX_RESULT_BYTES,
            CypherReadError,
            parse_cypher_request,
            validate_cypher_result,
        )

        if type(context) is not DevgraphRequestContext:
            raise ValueError("Cypher reads require a read credential context")
        compiled = parse_cypher_request(request_json)
        kwargs = {
            "headers": {"Authorization": f"Bearer {context.credential}"},
            "timeout": self._timeout, "json": json_codec.loads(compiled.canonical_request),
        }
        try:
            stream = getattr(self._transport, "stream", None)
            if callable(stream):
                # Real HTTP clients stream: enforce the response cap before
                # accumulating or decoding its JSON. Never follow redirects or
                # decompress a caller-controlled compressed response.
                with stream(
                    "POST", f"{self._base_url}/query/cypher", follow_redirects=False, **kwargs,
                ) as incoming:
                    if incoming.headers.get("content-encoding", "identity") != "identity":
                        raise DevgraphInvalidSuccessEnvelope
                    raw = bytearray()
                    for chunk in incoming.iter_bytes(chunk_size=8192):
                        if len(raw) + len(chunk) > MAX_RESULT_BYTES:
                            raise DevgraphInvalidSuccessEnvelope
                        raw.extend(chunk)
                    response = httpx.Response(
                        incoming.status_code, headers=incoming.headers, content=bytes(raw),
                    )
            else:
                # The transport protocol also supports bounded in-process test
                # adapters; the installed CLI always uses the streaming branch.
                response = self._transport.request(
                    "POST", f"{self._base_url}/query/cypher", **kwargs,
                )
        except DevgraphClientError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise DevgraphTimeout from None
        except Exception:
            raise DevgraphTransportError from None
        if response.status_code != 200:
            self._raise_problem(response)
        content_type = str(response.headers.get("content-type", "")).lower()
        if content_type.split(";", 1)[0].strip() != "application/json":
            raise DevgraphUnexpectedContentType
        raw = getattr(response, "content", None)
        if isinstance(raw, bytes) and len(raw) > MAX_RESULT_BYTES:
            raise DevgraphInvalidSuccessEnvelope
        try:
            value = response.json()
        except (ValueError, TypeError):
            raise DevgraphMalformedSuccess from None
        try:
            return validate_cypher_result(value, compiled)
        except CypherReadError:
            raise DevgraphInvalidSuccessEnvelope from None

    def execute_named_work(
        self, context: DevgraphWorkContext, *, request_json: bytes, idempotency_key: str
    ) -> MutationResult:
        from devgraph.work_requests import WorkRequest

        if not isinstance(context, DevgraphWorkContext):
            raise TypeError("named Work requires a secS projection context")
        request = WorkRequest.from_json(request_json)
        if (
            not isinstance(idempotency_key, str)
            or re.fullmatch(r"[A-Za-z0-9._~-]{16,128}", idempotency_key) is None
        ):
            raise ValueError("invalid named Work idempotency key")
        result = self._request(
            MutationResult,
            "POST",
            "/work-operations/v1",
            context=context,
            idempotency_key=idempotency_key,
            json=json_codec.loads(request.canonical),
        )
        kind, work_id = (
            ("Issue", request.payload["issue_id"])
            if request.operation == "convert"
            else (request.kind, request.id)
        )
        return self._expect_mutation(
            result,
            operation=request.authority_operation,
            subject_label=kind,
            subject_id=work_id,
            work_kind=kind,
        )

    def execute_arena(self, context: DevgraphWorkContext, *, request_json: bytes,
                      idempotency_key: str) -> ArenaMutationResult:
        from devgraph.arena_requests import ArenaRequest

        if not isinstance(context, DevgraphWorkContext):
            raise TypeError("Arena writes require a secS projection context")
        request = ArenaRequest.from_json(request_json)
        if not isinstance(idempotency_key, str) or re.fullmatch(
            r"[A-Za-z0-9._~-]{16,128}", idempotency_key
        ) is None:
            raise ValueError("invalid Arena idempotency key")
        result = self._request(ArenaMutationResult, "POST", "/arena-operations/v1",
            context=context, idempotency_key=idempotency_key,
            json=json_codec.loads(request.canonical))
        item = result.arena if request.kind == "Arena" else result.work
        if (result.receipt.operation != request.authority_operation
            or result.receipt.subject_label != request.kind
            or result.receipt.subject_id != request.id
            or item is not None and (item.kind, item.id) != (request.kind, request.id)):
            raise DevgraphInvalidSuccessEnvelope
        return result

    def get_arena(self, context: DevgraphRequestContext, *, arena_id: str) -> Arena:
        validate_work_object_id(arena_id)
        result = self._request(Arena, "GET", f"/arenas/{arena_id}", context=context)
        if result.id != arena_id:
            raise DevgraphInvalidSuccessEnvelope
        return result

    def list_arenas(self, context: DevgraphRequestContext, *, include_archived=False,
                    after_id=None, limit=50) -> ArenaList:
        validate_page_limit(limit)
        if type(include_archived) is not bool:
            raise ValueError("invalid Arena archive filter")
        query = {"include_archived": str(include_archived).lower(), "limit": limit}
        if after_id is not None:
            validate_work_object_id(after_id)
            query["after_id"] = after_id
        result = self._request(ArenaList, "GET", "/arenas?" + urlencode(query), context=context)
        ids = [item.id for item in result.items]
        if (len(ids) > limit or ids != sorted(set(ids))
            or after_id is not None and any(value <= after_id for value in ids)
            or not include_archived and any(item.archived for item in result.items)):
            raise DevgraphInvalidSuccessEnvelope
        return result

    def get_arena_members(self, context: DevgraphRequestContext, *, arena_id: str,
                          after_resource=None, limit=50) -> WorkObjectList:
        validate_work_object_id(arena_id)
        validate_page_limit(limit)
        query = {"limit": limit}
        after = None
        if after_resource is not None:
            if not isinstance(after_resource, str) or after_resource.count("/") != 1:
                raise ValueError("invalid Arena member cursor")
            kind, work_id = after_resource.split("/")
            if kind not in ("Initiative", "Task"):
                raise ValueError("invalid Arena member cursor")
            validate_work_object_id(work_id)
            after = (kind, work_id)
            query["after_resource"] = after_resource
        result = self._request(WorkObjectList, "GET",
            f"/arenas/{arena_id}/members?" + urlencode(query), context=context)
        keys = [(item.kind, item.id) for item in result.items]
        if (len(keys) > limit or keys != sorted(set(keys))
            or any(kind not in ("Initiative", "Task") for kind, _ in keys)
            or after is not None and any(key <= after for key in keys)):
            raise DevgraphInvalidSuccessEnvelope
        return result

    def get_work_arena(self, context: DevgraphRequestContext, *, kind: WorkKind,
                       work_id: str) -> ArenaMembership:
        if kind not in _WORK_KINDS:
            raise ValueError("invalid Work kind")
        validate_work_object_id(work_id)
        result = self._request(ArenaMembership, "GET", f"/work/{kind}/{work_id}/arena",
                               context=context)
        if result.arena is not None and (
            result.root_kind not in ("Initiative", "Task")
            or result.inherited != ((result.root_kind, result.root_id) != (kind, work_id))
        ) or result.arena is None and result.inherited:
            raise DevgraphInvalidSuccessEnvelope
        return result

    def create_work(
        self,
        context: DevgraphRequestContext,
        *,
        kind: WorkKind,
        work_id: str,
        title: str,
        idempotency_key: str,
        description: str = "",
        priority: int = 0,
        artifact_ids: Sequence[str] = (),
        external_link_ids: Sequence[str] = (),
    ) -> MutationResult:
        selected_kind = self._require_kind(kind)
        payload: dict[str, Any] = {"id": work_id, "title": title}
        if description != "":
            payload["description"] = description
        if priority != 0:
            payload["priority"] = priority
        frozen_artifact_ids = self._string_sequence("artifact_ids", artifact_ids)
        if frozen_artifact_ids:
            payload["artifact_ids"] = list(frozen_artifact_ids)
        frozen_external_link_ids = self._string_sequence("external_link_ids", external_link_ids)
        if frozen_external_link_ids:
            payload["external_link_ids"] = list(frozen_external_link_ids)
        result = self._request(
            MutationResult,
            "POST",
            f"/work/{selected_kind}",
            context=context,
            idempotency_key=self._require_key(idempotency_key),
            json=payload,
        )
        return self._expect_mutation(
            result,
            operation="create_work_object",
            subject_label=selected_kind,
            subject_id=work_id,
            work_kind=selected_kind,
            work_status="draft",
        )

    def get_work(
        self,
        context: DevgraphRequestContext,
        *,
        kind: WorkKind,
        work_id: str,
    ) -> WorkObject:
        selected_kind = self._require_kind(kind)
        work = self._request(
            WorkObject,
            "GET",
            f"/work/{selected_kind}/{quote(work_id, safe='')}",
            context=context,
        )
        return self._expect_work_kind(work, selected_kind)

    def get_supporting_material(
        self,
        context: DevgraphRequestContext,
        *,
        kind: WorkKind,
        work_id: str,
        limit: int = 50,
        after: str | None = None,
    ) -> SupportingMaterialEnvelope:
        selected_kind = self._require_kind(kind)
        validate_work_object_id(work_id)
        validate_page_limit(limit)
        params = {"limit": str(limit)}
        if after is not None:
            if not isinstance(after, str) or not 1 <= len(after) <= 4096:
                raise ValueError("invalid supporting-material cursor")
            params["after"] = after
        result = self._request(
            SupportingMaterialEnvelope,
            "GET",
            f"/work/{selected_kind}/{quote(work_id, safe='')}/supporting-material"
            f"?{urlencode(params)}",
            context=context,
        )
        if (
            result.work.kind != selected_kind
            or result.work.id != work_id
            or len(result.items) > limit
        ):
            raise DevgraphInvalidSuccessEnvelope
        return result


    def get_work_document(
        self,
        context: DevgraphRequestContext,
        *,
        kind: WorkKind,
        work_id: str,
        artifact_id: str,
    ) -> WorkDocumentEnvelope:
        selected_kind = self._require_kind(kind)
        validate_work_object_id(work_id)
        validate_work_object_id(artifact_id)
        result = self._request(
            WorkDocumentEnvelope,
            "GET",
            f"/work/{selected_kind}/{quote(work_id, safe='')}/supporting-material/Artifact/"
            f"{quote(artifact_id, safe='')}/document",
            context=context,
        )
        if result.artifact_id != artifact_id:
            raise DevgraphInvalidSuccessEnvelope
        return result

    def list_work(
        self,
        context: DevgraphRequestContext,
        *,
        kind: WorkKind,
        include_archived: bool = False,
        descending: bool = False,
        after_id: str | None = None,
        limit: int = 50,
    ) -> WorkObjectList:
        selected_kind = self._require_kind(kind)
        if not isinstance(include_archived, bool):
            raise ValueError("include_archived must be boolean")
        if not isinstance(descending, bool):
            raise ValueError("descending must be boolean")
        validate_page_limit(limit)
        params = {"include_archived": "true" if include_archived else "false"}
        # Keep the original request and strict response envelope for defaults.
        if descending:
            params["descending"] = "true"
        if after_id is not None:
            params["after_id"] = validate_work_object_id(after_id)
        if limit != 50:
            params["limit"] = str(limit)
        result = self._request(
            WorkObjectList,
            "GET",
            f"/work/{selected_kind}?{urlencode(params)}",
            context=context,
        )
        if any(item.kind != selected_kind for item in result.items):
            raise DevgraphInvalidSuccessEnvelope
        return result

    def patch_work(
        self,
        context: DevgraphRequestContext,
        *,
        kind: WorkKind,
        work_id: str,
        expected_version: int,
        idempotency_key: str,
        title: str | None = None,
        description: str | None = None,
        priority: int | None = None,
        artifact_ids: Sequence[str] | None = None,
        external_link_ids: Sequence[str] | None = None,
    ) -> MutationResult:
        selected_kind = self._require_kind(kind)
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if priority is not None:
            payload["priority"] = priority
        if artifact_ids is not None:
            payload["artifact_ids"] = list(self._string_sequence("artifact_ids", artifact_ids))
        if external_link_ids is not None:
            payload["external_link_ids"] = list(
                self._string_sequence("external_link_ids", external_link_ids)
            )
        if not payload:
            raise ValueError("patch must contain at least one field")
        result = self._request(
            MutationResult,
            "PATCH",
            f"/work/{selected_kind}/{quote(work_id, safe='')}",
            context=context,
            idempotency_key=self._require_key(idempotency_key),
            if_match=self._quoted_version(expected_version),
            json=payload,
        )
        return self._expect_mutation(
            result,
            operation="update_work_object_content",
            subject_label=selected_kind,
            subject_id=work_id,
            work_kind=selected_kind,
        )

    def transition_work_status(
        self,
        context: DevgraphRequestContext,
        *,
        kind: WorkKind,
        work_id: str,
        status: WorkStatus,
        idempotency_key: str,
    ) -> MutationResult:
        selected_kind = self._require_kind(kind)
        selected_status = self._require_status(status)
        result = self._request(
            MutationResult,
            "POST",
            f"/work/{selected_kind}/{quote(work_id, safe='')}/status",
            context=context,
            idempotency_key=self._require_key(idempotency_key),
            json={"status": selected_status},
        )
        return self._expect_mutation(
            result,
            operation="transition_work_object_status",
            subject_label=selected_kind,
            subject_id=work_id,
            work_kind=selected_kind,
            work_status=selected_status,
        )

    def archive_work(
        self,
        context: DevgraphRequestContext,
        *,
        kind: WorkKind,
        work_id: str,
        idempotency_key: str,
    ) -> MutationResult:
        selected_kind = self._require_kind(kind)
        result = self._request(
            MutationResult,
            "POST",
            f"/work/{selected_kind}/{quote(work_id, safe='')}/archive",
            context=context,
            idempotency_key=self._require_key(idempotency_key),
        )
        return self._expect_mutation(
            result,
            operation="archive_work_object",
            subject_label=selected_kind,
            subject_id=work_id,
            work_kind=selected_kind,
            work_status="archived",
        )

    def get_work_relationships(
        self,
        context: DevgraphRequestContext,
        *,
        kind: WorkKind,
        work_id: str,
        relationship: str,
        after_resource: str | None = None,
        limit: int = 50,
    ) -> WorkObjectList:
        selected_kind = self._require_kind(kind)
        validate_work_object_id(work_id)
        if relationship not in {
            "children",
            "parent",
            "dependencies",
            "dependents",
            "blockers",
            "blocked",
        }:
            raise ValueError("invalid Work relationship")
        if relationship in {"blockers", "blocked"} and kind != "Task":
            raise ValueError("blocker relationships require Task")
        validate_page_limit(limit)
        params = {"limit": str(limit)}
        if after_resource is not None:
            if not isinstance(after_resource, str) or after_resource.count("/") != 1:
                raise ValueError("invalid relationship cursor")
            target_kind, target_id = after_resource.split("/")
            self._require_kind(target_kind)
            validate_work_object_id(target_id)
            params["after_resource"] = after_resource
        result = self._request(
            WorkObjectList,
            "GET",
            f"/work/{selected_kind}/{quote(work_id, safe='')}/relationships/"
            f"{relationship}?{urlencode(params)}",
            context=context,
        )
        if relationship in {"blockers", "blocked"} and any(
            item.kind != "Task" for item in result.items
        ):
            raise DevgraphInvalidSuccessEnvelope
        return result

    def get_work_children(
        self,
        context: DevgraphRequestContext,
        *,
        kind: WorkKind,
        work_id: str,
    ) -> WorkObjectCollection:
        selected_kind = self._require_kind(kind)
        return self._request(
            WorkObjectCollection,
            "GET",
            f"/work/{selected_kind}/{quote(work_id, safe='')}/children",
            context=context,
        )

    def get_task_blockers(
        self,
        context: DevgraphRequestContext,
        *,
        task_id: str,
    ) -> WorkObjectCollection:
        result = self._request(
            WorkObjectCollection,
            "GET",
            f"/tasks/{quote(task_id, safe='')}/blockers",
            context=context,
        )
        if any(item.kind != "Task" for item in result.items):
            raise DevgraphInvalidSuccessEnvelope
        return result

    def accept_proposal(
        self,
        context: DevgraphRequestContext,
        *,
        proposal_id: str,
        decision_id: str,
        decision_title: str,
        idempotency_key: str,
    ) -> MutationResult:
        result = self._request(
            MutationResult,
            "POST",
            f"/proposals/{quote(proposal_id, safe='')}/accept",
            context=context,
            idempotency_key=self._require_key(idempotency_key),
            json={"decision_id": decision_id, "decision_title": decision_title},
        )
        return self._expect_mutation(
            result,
            operation="accept_proposal",
            subject_label="Proposal",
            subject_id=proposal_id,
            work_kind="Proposal",
            work_status="accepted",
        )

    def convert_proposal(
        self,
        context: DevgraphRequestContext,
        *,
        proposal_id: str,
        issue_id: str,
        idempotency_key: str,
    ) -> MutationResult:
        result = self._request(
            MutationResult,
            "POST",
            f"/proposals/{quote(proposal_id, safe='')}/convert",
            context=context,
            idempotency_key=self._require_key(idempotency_key),
            json={"issue_id": issue_id},
        )
        return self._expect_mutation(
            result,
            operation="convert_accepted_proposal_to_issue",
            subject_label="Issue",
            subject_id=issue_id,
            work_kind="Issue",
            work_status="draft",
        )

    # Compatibility wrappers retained for the original bounded Issue proof and
    # the four-operation remote gateway contract.
    def create_issue(
        self,
        context: DevgraphRequestContext,
        *,
        work_id: str,
        title: str,
        idempotency_key: str,
    ) -> MutationResult:
        return self.create_work(
            context,
            kind="Issue",
            work_id=work_id,
            title=title,
            idempotency_key=idempotency_key,
        )

    def get_issue(self, context: DevgraphRequestContext, *, work_id: str) -> WorkObject:
        return self.get_work(context, kind="Issue", work_id=work_id)

    def list_issues(
        self,
        context: DevgraphRequestContext,
        *,
        include_archived: bool = False,
    ) -> WorkObjectList:
        return self.list_work(
            context,
            kind="Issue",
            include_archived=include_archived,
        )

    def transition_issue_to_review(
        self,
        context: DevgraphRequestContext,
        *,
        work_id: str,
        idempotency_key: str,
    ) -> MutationResult:
        return self.transition_work_status(
            context,
            kind="Issue",
            work_id=work_id,
            status="review",
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def _require_kind(value: str) -> WorkKind:
        if value not in _WORK_KINDS:
            raise ValueError("unsupported work kind")
        return cast(WorkKind, value)

    @staticmethod
    def _require_status(value: str) -> WorkStatus:
        if value not in _WORK_STATUSES:
            raise ValueError("unsupported work status")
        return cast(WorkStatus, value)

    @staticmethod
    def _require_key(value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("idempotency_key must not be empty")
        return value

    @staticmethod
    def _quoted_version(value: int) -> str:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("expected_version must be a positive integer")
        return f'"{value}"'

    @staticmethod
    def _string_sequence(name: str, value: Sequence[str]) -> tuple[str, ...]:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValueError(f"{name} must be a sequence of strings")
        frozen = tuple(value)
        if not all(isinstance(item, str) for item in frozen):
            raise ValueError(f"{name} must be a sequence of strings")
        return frozen

    @staticmethod
    def _expect_work_kind(work: WorkObject, kind: WorkKind) -> WorkObject:
        if work.kind != kind:
            raise DevgraphInvalidSuccessEnvelope
        return work

    @classmethod
    def _expect_mutation(
        cls,
        result: MutationResult,
        *,
        operation: MutationOperation,
        subject_label: WorkKind,
        subject_id: str,
        work_kind: WorkKind,
        work_status: WorkStatus | None = None,
    ) -> MutationResult:
        receipt = result.receipt
        if (
            receipt.operation != operation
            or receipt.subject_label != subject_label
            or receipt.subject_id != subject_id
            or (
                result.work is not None
                and (
                    result.work.id != subject_id
                    or result.work.kind != work_kind
                    or (work_status is not None and result.work.status != work_status)
                )
            )
        ):
            raise DevgraphInvalidSuccessEnvelope
        return result

    def _request(
        self,
        model: type[BaseModel],
        method: str,
        path: str,
        *,
        context: DevgraphRequestContext,
        idempotency_key: str | None = None,
        if_match: str | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        if isinstance(context, DevgraphWorkContext):
            if method != "POST" or path not in ("/work-operations/v1", "/arena-operations/v1"):
                raise ValueError("named Work proof has an invalid transport target")
            headers = {
                "X-Devgraph-Work-Authority": base64.urlsafe_b64encode(context.projection_json)
                .rstrip(b"=")
                .decode("ascii")
            }
        else:
            headers = {"Authorization": f"Bearer {context.credential}"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        if if_match is not None:
            headers["If-Match"] = if_match
        kwargs: dict[str, Any] = {"headers": headers, "timeout": self._timeout}
        if json is not None:
            kwargs["json"] = json
        try:
            response = self._transport.request(method, f"{self._base_url}{path}", **kwargs)
        except (TimeoutError, httpx.TimeoutException):
            raise DevgraphTimeout from None
        except Exception:
            raise DevgraphTransportError from None
        if 200 <= response.status_code < 300:
            content_type = str(response.headers.get("content-type", "")).lower()
            if content_type.split(";", 1)[0].strip() != "application/json":
                raise DevgraphUnexpectedContentType
            try:
                body = response.json()
            except (ValueError, TypeError):
                raise DevgraphMalformedSuccess from None
            try:
                return model.model_validate(body)
            except (ValidationError, TypeError):
                raise DevgraphInvalidSuccessEnvelope from None
        self._raise_problem(response)

    @staticmethod
    def _raise_problem(response: SyncResponse) -> None:
        content_type = str(response.headers.get("content-type", "")).lower()
        if content_type.split(";", 1)[0].strip() != "application/problem+json":
            raise DevgraphMalformedProblem
        try:
            body = _ProblemBody.model_validate(response.json())
        except (ValidationError, ValueError, TypeError):
            raise DevgraphMalformedProblem from None
        if body.status != response.status_code:
            raise DevgraphMalformedProblem
        raise DevgraphProblem(**body.model_dump())
