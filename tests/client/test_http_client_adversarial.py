from __future__ import annotations

import json
from typing import Any

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
)


class AdversarialResponse:
    def __init__(self, status: int, body: Any, content_type: str) -> None:
        self.status_code = status
        self._body = body
        self.headers = {"content-type": content_type}

    def json(self) -> Any:
        if isinstance(self._body, BaseException):
            raise self._body
        return self._body


class CountingTransport:
    def __init__(self, result: AdversarialResponse | BaseException) -> None:
        self.result = result
        self.calls = 0

    def request(self, method: str, url: str, **kwargs: Any) -> AdversarialResponse:
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def invoke(result: AdversarialResponse | BaseException) -> tuple[Exception, int]:
    transport = CountingTransport(result)
    client = DevgraphHttpClient(transport=transport, base_url="http://in-process", timeout=1)
    with pytest.raises(Exception) as captured:
        client.get_issue(DevgraphRequestContext(credential="opaque-adversarial"), work_id="x")
    return captured.value, transport.calls


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            AdversarialResponse(200, ValueError("raw-json"), "application/json"),
            DevgraphMalformedSuccess,
        ),
        (AdversarialResponse(200, [], "application/json"), DevgraphInvalidSuccessEnvelope),
        (
            AdversarialResponse(200, {"private": "raw-body-marker"}, "application/json"),
            DevgraphInvalidSuccessEnvelope,
        ),
        (
            AdversarialResponse(200, {}, "application/problem+json"),
            DevgraphUnexpectedContentType,
        ),
        (
            AdversarialResponse(401, ValueError("raw-json"), "application/problem+json"),
            DevgraphMalformedProblem,
        ),
        (AdversarialResponse(401, [], "application/problem+json"), DevgraphMalformedProblem),
        (
            AdversarialResponse(
                401,
                {
                    "status": 403,
                    "title": "Forbidden",
                    "type": "about:blank",
                },
                "application/problem+json",
            ),
            DevgraphMalformedProblem,
        ),
        (AdversarialResponse(500, {}, "text/plain"), DevgraphMalformedProblem),
    ],
)
def test_malformed_responses_fail_closed_without_raw_echo_or_retry(
    response: AdversarialResponse, expected: type[Exception]
) -> None:
    error, calls = invoke(response)
    assert isinstance(error, expected)
    assert calls == 1
    rendered = f"{error!s} {error!r}"
    assert "raw-json" not in rendered and "raw-body-marker" not in rendered


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (TimeoutError("raw-timeout-marker"), DevgraphTimeout),
        (RuntimeError("raw-transport-marker"), DevgraphTransportError),
    ],
)
def test_transport_failures_are_distinct_safe_and_single_attempt(
    raised: BaseException, expected: type[Exception]
) -> None:
    error, calls = invoke(raised)
    assert isinstance(error, expected)
    assert calls == 1
    assert "raw-" not in f"{error!s} {error!r}"


def test_parsed_problem_retains_only_typed_safe_fields() -> None:
    error, calls = invoke(
        AdversarialResponse(
            403,
            {
                "status": 403,
                "title": "Forbidden",
                "type": "about:blank",
                "detail": "scope not granted",
                "correlation_id": "safe-correlation",
            },
            "application/problem+json",
        )
    )
    assert calls == 1 and isinstance(error, DevgraphProblem)
    assert vars(error) == {
        "status": 403,
        "title": "Forbidden",
        "type": "about:blank",
        "detail": "scope not granted",
        "correlation_id": "safe-correlation",
    }
    assert "opaque-adversarial" not in json.dumps(vars(error))


def test_credential_validation_error_and_context_projection_hide_secret_input() -> None:
    marker = "opaque-invalid-secret-marker"

    with pytest.raises(ValidationError) as captured:
        DevgraphRequestContext(credential={"secret": marker})  # type: ignore[arg-type]

    assert marker not in str(captured.value)
    assert marker not in repr(captured.value)
    context = DevgraphRequestContext(credential=marker)
    assert marker not in repr(context)
    assert marker not in context.model_dump_json()


def test_raw_collection_with_non_work_kind_fails_closed() -> None:
    body = [
        {
            "id": "decision-1",
            "kind": "Decision",
            "title": "not bounded work",
            "description": "",
            "status": "accepted",
            "version": 1,
            "priority": 0,
            "artifact_ids": [],
            "external_link_ids": [],
        }
    ]
    transport = CountingTransport(AdversarialResponse(200, body, "application/json"))
    client = DevgraphHttpClient(
        transport=transport,
        base_url="http://in-process",
        timeout=1,
    )

    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client.get_work_children(
            DevgraphRequestContext(credential="opaque-adversarial"),
            kind="Project",
            work_id="project-1",
        )

    assert transport.calls == 1
