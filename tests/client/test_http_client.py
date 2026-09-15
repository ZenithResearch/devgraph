from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from devgraph.client import (
    DevgraphHttpClient,
    DevgraphInvalidSuccessEnvelope,
    DevgraphMalformedProblem,
    DevgraphMalformedSuccess,
    DevgraphProblem,
    DevgraphRequestContext,
    DevgraphTimeout,
    DevgraphTransportError,
    DevgraphUnexpectedContentType,
    MutationResult,
)


class Response:
    def __init__(
        self,
        status_code: int,
        body: Any,
        *,
        content_type: str = "application/json",
        malformed_json: bool = False,
    ) -> None:
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._body = body
        self._malformed_json = malformed_json

    def json(self) -> Any:
        if self._malformed_json:
            raise json.JSONDecodeError("bad", "", 0)
        return self._body


class RecordingTransport:
    def __init__(self, responses: list[Response | BaseException]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def work(*, status: str = "draft", version: int = 1) -> dict[str, Any]:
    return {
        "id": "issue-1",
        "kind": "Issue",
        "title": "Bounded proof",
        "description": "",
        "status": status,
        "version": version,
        "priority": 0,
        "artifact_ids": [],
        "external_link_ids": [],
    }


def receipt(*, duplicate: bool = False) -> dict[str, Any]:
    return {
        "receipt_id": "receipt-1",
        "operation": "create_work_object",
        "subject_label": "Issue",
        "subject_id": "issue-1",
        "receipt_status": "pending",
        "duplicate": duplicate,
        "correlation_id": "corr-1",
    }


def test_exact_four_methods_forward_bounded_http_contract() -> None:
    transport = RecordingTransport(
        [
            Response(201, {"work": work(), "receipt": receipt()}),
            Response(200, work()),
            Response(200, {"items": [work()]}),
            Response(
                200,
                {
                    "work": work(status="review", version=2),
                    "receipt": {**receipt(), "operation": "transition_work_object_status"},
                },
            ),
        ]
    )
    client = DevgraphHttpClient(transport=transport, base_url="http://in-process", timeout=2.5)
    context = DevgraphRequestContext(credential="opaque-marker")

    created = client.create_issue(
        context, work_id="issue-1", title="Bounded proof", idempotency_key="create-key"
    )
    got = client.get_issue(context, work_id="issue-1")
    listed = client.list_issues(context)
    transitioned = client.transition_issue_to_review(
        context, work_id="issue-1", idempotency_key="status-key"
    )

    assert isinstance(created, MutationResult)
    assert got.id == "issue-1"
    assert listed.items == (got,)
    assert transitioned.work is not None and transitioned.work.status == "review"
    assert [call["method"] for call in transport.calls] == ["POST", "GET", "GET", "POST"]
    assert [call["url"] for call in transport.calls] == [
        "http://in-process/work/Issue",
        "http://in-process/work/Issue/issue-1",
        "http://in-process/work/Issue?include_archived=false",
        "http://in-process/work/Issue/issue-1/status",
    ]
    assert transport.calls[0]["headers"] == {
        "Authorization": "Bearer opaque-marker",
        "Idempotency-Key": "create-key",
    }
    assert transport.calls[1]["headers"] == {"Authorization": "Bearer opaque-marker"}
    assert transport.calls[2]["headers"] == {"Authorization": "Bearer opaque-marker"}
    assert transport.calls[3]["headers"] == {
        "Authorization": "Bearer opaque-marker",
        "Idempotency-Key": "status-key",
    }
    assert transport.calls[0]["json"] == {"id": "issue-1", "title": "Bounded proof"}
    assert transport.calls[3]["json"] == {"status": "review"}
    assert all(call["timeout"] == 2.5 for call in transport.calls)


def test_duplicate_is_preserved_without_refetch_or_reconstruction() -> None:
    transport = RecordingTransport(
        [Response(200, {"work": None, "receipt": receipt(duplicate=True)})]
    )
    client = DevgraphHttpClient(transport=transport, base_url="http://in-process", timeout=1)
    result = client.create_issue(
        DevgraphRequestContext(credential="opaque"),
        work_id="issue-1",
        title="Bounded proof",
        idempotency_key="same-key",
    )
    assert result.work is None
    assert result.receipt.duplicate is True
    assert len(transport.calls) == 1


def test_values_are_strict_and_immutable_and_configuration_is_bounded() -> None:
    context = DevgraphRequestContext(credential="opaque-secret")
    assert "opaque-secret" not in repr(context)
    assert context.model_dump() == {}
    assert context.model_dump_json() == "{}"
    with pytest.raises((FrozenInstanceError, TypeError, ValidationError)):
        context.credential = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError):
        DevgraphRequestContext(credential="")
    with pytest.raises(ValueError):
        DevgraphHttpClient(transport=RecordingTransport([]), base_url="", timeout=1)
    with pytest.raises(ValueError):
        DevgraphHttpClient(transport=RecordingTransport([]), base_url="http://x", timeout=0)
    client = DevgraphHttpClient(transport=RecordingTransport([]), base_url="http://x", timeout=1)
    with pytest.raises(ValueError):
        client.create_issue(context, work_id="x", title="x", idempotency_key="")


@pytest.mark.parametrize("base_url", [None, 3, "", "   ", "\t\n"])
def test_base_url_must_be_a_non_blank_string(base_url: object) -> None:
    with pytest.raises(ValueError):
        DevgraphHttpClient(
            transport=RecordingTransport([]),
            base_url=base_url,  # type: ignore[arg-type]
            timeout=1,
        )


@pytest.mark.parametrize("timeout", [True, False, 0, -1, float("nan"), float("inf"), -float("inf")])
def test_timeout_must_be_a_finite_positive_non_boolean_number(timeout: object) -> None:
    with pytest.raises(ValueError):
        DevgraphHttpClient(
            transport=RecordingTransport([]),
            base_url="http://x",
            timeout=timeout,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (Response(200, None, malformed_json=True), DevgraphMalformedSuccess),
        (Response(200, {"id": "x"}), DevgraphInvalidSuccessEnvelope),
        (Response(200, {**work(), "unknown": True}), DevgraphInvalidSuccessEnvelope),
        (Response(200, work(), content_type="text/plain"), DevgraphUnexpectedContentType),
        (
            Response(401, None, malformed_json=True, content_type="application/problem+json"),
            DevgraphMalformedProblem,
        ),
        (
            Response(
                401,
                {"title": "Unauthenticated"},
                content_type="application/problem+json",
            ),
            DevgraphMalformedProblem,
        ),
        (Response(500, {"title": "x"}, content_type="application/json"), DevgraphMalformedProblem),
    ],
)
def test_fail_closed_response_categories(response: Response, error: type[Exception]) -> None:
    client = DevgraphHttpClient(
        transport=RecordingTransport([response]), base_url="http://in-process", timeout=1
    )
    with pytest.raises(error):
        client.get_issue(DevgraphRequestContext(credential="opaque"), work_id="issue-1")


def test_safe_problem_mapping_and_repr() -> None:
    transport = RecordingTransport(
        [
            Response(
                403,
                {
                    "type": "about:blank",
                    "title": "Forbidden",
                    "status": 403,
                    "detail": "scope not granted",
                    "correlation_id": "corr-safe",
                },
                content_type="application/problem+json; charset=utf-8",
            )
        ]
    )
    client = DevgraphHttpClient(transport=transport, base_url="http://in-process", timeout=1)
    with pytest.raises(DevgraphProblem) as captured:
        client.get_issue(DevgraphRequestContext(credential="opaque-secret"), work_id="issue-1")
    problem = captured.value
    assert problem.status == 403 and problem.title == "Forbidden"
    assert problem.correlation_id == "corr-safe"
    assert "opaque-secret" not in str(problem)
    assert "opaque-secret" not in repr(problem)


@pytest.mark.parametrize(
    ("raised", "error"),
    [
        (TimeoutError("private timeout"), DevgraphTimeout),
        (RuntimeError("private transport"), DevgraphTransportError),
    ],
)
def test_transport_errors_are_typed_safe_and_not_retried(
    raised: BaseException, error: type[Exception]
) -> None:
    transport = RecordingTransport([raised])
    client = DevgraphHttpClient(transport=transport, base_url="http://in-process", timeout=1)
    with pytest.raises(error) as captured:
        client.get_issue(DevgraphRequestContext(credential="opaque-secret"), work_id="issue-1")
    assert len(transport.calls) == 1
    rendered = f"{captured.value!s} {captured.value!r}"
    assert "private" not in rendered and "opaque-secret" not in rendered


def test_real_httpx_timeout_is_safe_timeout_without_retry() -> None:
    request = httpx.Request("GET", "http://in-process/work/Issue/issue-1")
    transport = RecordingTransport([httpx.ReadTimeout("private locked timeout", request=request)])
    client = DevgraphHttpClient(transport=transport, base_url="http://in-process", timeout=1)

    with pytest.raises(DevgraphTimeout) as captured:
        client.get_issue(DevgraphRequestContext(credential="opaque-secret"), work_id="issue-1")

    assert len(transport.calls) == 1
    rendered = f"{captured.value!s} {captured.value!r}"
    assert "private locked timeout" not in rendered
    assert "opaque-secret" not in rendered
