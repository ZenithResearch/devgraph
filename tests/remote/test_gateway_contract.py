from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, PydanticUserError, TypeAdapter

from devgraph.client import (
    DevgraphInvalidSuccessEnvelope,
    DevgraphMalformedProblem,
    DevgraphMalformedSuccess,
    DevgraphProblem,
    DevgraphRequestContext,
    DevgraphTimeout,
    DevgraphTransportError,
    DevgraphUnexpectedContentType,
)
from devgraph.remote.contracts import RemoteCredentialValidationError, RemoteIngressCredential
from devgraph.remote.gateway import RemoteGateway
from devgraph.remote.responses import TransportSafeProjector


@dataclass
class Call:
    method: str
    arguments: dict[str, Any]


class SpyClient:
    def __init__(self) -> None:
        self.calls: list[Call] = []

    def _record(self, method: str, context: DevgraphRequestContext, **kwargs: Any) -> object:
        assert context.credential == "opaque-test-credential"
        self.calls.append(Call(method, kwargs))
        return {"client_method": method}

    def create_issue(self, context: DevgraphRequestContext, **kwargs: Any) -> object:
        return self._record("create_issue", context, **kwargs)

    def get_issue(self, context: DevgraphRequestContext, **kwargs: Any) -> object:
        return self._record("get_issue", context, **kwargs)

    def list_issues(self, context: DevgraphRequestContext, **kwargs: Any) -> object:
        return self._record("list_issues", context, **kwargs)

    def transition_issue_to_review(self, context: DevgraphRequestContext, **kwargs: Any) -> object:
        return self._record("transition_issue_to_review", context, **kwargs)


class RecordingProjector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.failures: list[tuple[str, str, str, str | None]] = []

    def success(self, *, request_id: str, operation: str, value: object) -> dict[str, str]:
        self.calls.append((request_id, operation, value))
        return {"request_id": request_id, "operation": operation}

    def failure(
        self,
        *,
        request_id: str,
        operation: str,
        reason_code: str,
        correlation_id: str | None = None,
    ) -> dict[str, str]:
        self.failures.append((request_id, operation, reason_code, correlation_id))
        return {
            "request_id": request_id,
            "operation": operation,
            "reason_code": reason_code,
        }


def gateway() -> tuple[RemoteGateway, SpyClient, RecordingProjector]:
    client = SpyClient()
    projector = RecordingProjector()
    value = RemoteGateway(
        client=client,
        request_id_factory=lambda: "rmt_abcdefghijklmnopqrstuvwxyz",
        projector=projector,
    )
    return value, client, projector


def test_transient_credentials_are_hidden_from_repr_and_serialization() -> None:
    secret = "opaque-secret-marker"

    ingress = RemoteIngressCredential(credential=secret)
    context = DevgraphRequestContext(credential=secret)

    for holder in (ingress, context):
        assert secret not in repr(holder)
        assert secret not in str(holder)
        assert secret not in holder.model_dump_json()
        assert secret not in repr(holder.model_dump())


CANARY = "remote-credential-canary-marker"


def _assert_safe_credential_error(error: RemoteCredentialValidationError) -> None:
    assert error.__context__ is None
    assert error.__cause__ is None
    for value in (
        error.args, str(error), repr(error), error.errors(),
        error.errors(include_input=True, include_context=True),
        json.loads(error.json()), json.loads(error.json(include_input=True)),
    ):
        # The marker itself has no escape characters. Inspect the decoded JSON,
        # not just repr of a canary containing a newline (the original blind spot).
        assert CANARY not in str(value)
    for details in (error.errors(), json.loads(error.json())):
        assert len(details) == 1
        assert details[0]["type"] == "remote_credential_invalid"
        assert "input" not in details[0]
        assert "ctx" not in details[0]


@pytest.mark.parametrize("entrypoint", ["constructor", "python", "json"])
@pytest.mark.parametrize(
    "payload",
    [
        {"credential": CANARY + "\n"},
        {"credential": CANARY + "x" * 4096},
        {"credential": CANARY + "é" * 2048},
        {"credential": CANARY + "\ud800"},
        {"credential": {"nested": CANARY}},
        {"credential": [CANARY]},
        {"credential": CANARY, "extra": CANARY},
        {CANARY: CANARY},
        {"credential": ""},
        {},
    ],
)
def test_rejected_credentials_never_enter_structured_errors(
    entrypoint: str, payload: dict[str, object],
) -> None:
    with pytest.raises(RemoteCredentialValidationError) as captured:
        if entrypoint == "constructor":
            RemoteIngressCredential(**payload)
        elif entrypoint == "python":
            RemoteIngressCredential.model_validate(payload)
        else:
            RemoteIngressCredential.model_validate_json(json.dumps(payload))

    _assert_safe_credential_error(captured.value)


@pytest.mark.parametrize("entrypoint", ["python", "json"])
@pytest.mark.parametrize("value", [CANARY, [CANARY], 1, True, None])
def test_non_object_credential_input_is_rejected_safely(entrypoint: str, value: object) -> None:
    with pytest.raises(RemoteCredentialValidationError) as captured:
        if entrypoint == "python":
            RemoteIngressCredential.model_validate(value)
        else:
            RemoteIngressCredential.model_validate_json(json.dumps(value))

    _assert_safe_credential_error(captured.value)


@pytest.mark.parametrize(
    "data",
    [
        '{"credential": "' + CANARY,
        '{"credential": "' + CANARY + '"} trailing',
        ('{"credential": "' + CANARY).encode(),
        bytearray(('{"credential": "' + CANARY).encode()),
        b'{"credential": "' + CANARY.encode() + b'\xff"}',
        {"credential": CANARY},
        '[' * 2000 + '"' + CANARY + '"' + ']' * 2000,
    ],
)
def test_malformed_credential_json_has_no_raw_error_context(data: object) -> None:
    with pytest.raises(RemoteCredentialValidationError) as captured:
        RemoteIngressCredential.model_validate_json(data)

    _assert_safe_credential_error(captured.value)


def test_credential_holder_rejects_generic_pydantic_adapters() -> None:
    with pytest.raises(PydanticUserError, match="explicit validation API"):
        TypeAdapter(RemoteIngressCredential)
    with pytest.raises(PydanticUserError, match="explicit validation API"):
        TypeAdapter(list[RemoteIngressCredential])
    with pytest.raises(PydanticUserError, match="explicit validation API"):
        class OuterModel(BaseModel):
            holder: RemoteIngressCredential


@pytest.mark.parametrize("secret", ["x", "x" * 4096, "é" * 2048])
def test_supported_credential_entrypoints_preserve_opaque_value_and_hide_dumps(secret: str) -> None:
    data = json.dumps({"credential": secret})
    holders = [
        RemoteIngressCredential(credential=secret),
        RemoteIngressCredential.model_validate({"credential": secret}),
        RemoteIngressCredential.model_validate_json(data),
        RemoteIngressCredential.model_validate_json(data.encode()),
        RemoteIngressCredential.model_validate_json(bytearray(data.encode())),
    ]
    for holder in holders:
        assert holder.credential == secret
        assert RemoteIngressCredential.model_validate(holder) is holder
        assert holder.model_dump(include={"credential"}) == {}
        assert json.loads(holder.model_dump_json(include={"credential"})) == {}
        assert repr(holder) == "RemoteIngressCredential()"
        with pytest.raises(AttributeError, match="frozen"):
            holder.credential = CANARY
        with pytest.raises(AttributeError, match="frozen"):
            holder._credential = CANARY
        with pytest.raises(AttributeError, match="frozen"):
            del holder._credential


@pytest.mark.parametrize("credential", [CANARY + "\n", {"nested": CANARY}, [CANARY]])
def test_invalid_credential_fails_before_dispatch_without_echo(credential: object) -> None:
    remote, client, projector = gateway()

    response = remote.dispatch(
        {"operation": "get_issue", "arguments": {"work_id": "safe-id"}},
        credential=credential,
    )

    assert response["reason_code"] == "invalid_remote_request"
    assert client.calls == []
    assert projector.calls == []
    assert CANARY not in json.dumps(response)
    assert CANARY not in repr(projector.failures)


def test_dispatches_each_closed_operation_to_exact_client_method() -> None:
    remote, client, projector = gateway()
    credential = "opaque-test-credential"
    cases = (
        (
            {
                "operation": "create_issue",
                "arguments": {
                    "work_id": "issue-remote-one",
                    "title": "Remote title",
                    "idempotency_key": "remote-create-one",
                },
            },
            {
                "work_id": "issue-remote-one",
                "title": "Remote title",
                "idempotency_key": "remote-create-one",
            },
        ),
        (
            {"operation": "get_issue", "arguments": {"work_id": "issue-remote-one"}},
            {"work_id": "issue-remote-one"},
        ),
        ({"operation": "list_issues", "arguments": {}}, {"include_archived": False}),
        (
            {
                "operation": "transition_issue_to_review",
                "arguments": {
                    "work_id": "issue-remote-one",
                    "idempotency_key": "remote-review-one",
                },
            },
            {"work_id": "issue-remote-one", "idempotency_key": "remote-review-one"},
        ),
    )

    for payload, expected_arguments in cases:
        response = remote.dispatch(payload, credential=credential)
        assert response == {
            "request_id": "rmt_abcdefghijklmnopqrstuvwxyz",
            "operation": payload["operation"],
        }
        assert client.calls[-1] == Call(payload["operation"], expected_arguments)
        assert projector.calls[-1][0:2] == (
            "rmt_abcdefghijklmnopqrstuvwxyz",
            payload["operation"],
        )


def test_missing_credential_rejects_before_payload_or_client() -> None:
    remote, client, _ = gateway()

    response = remote.dispatch({"operation": object()}, credential="")

    assert response["reason_code"] == "credential_required"
    assert response["operation"] == "unrecognized"
    assert client.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "unknown-operation", "arguments": {}},
        {"operation": "get_issue", "arguments": {"work_id": "safe-id", "extra": "x"}},
        {"operation": "get_issue", "arguments": {"work_id": {"nested": "x"}}},
        {"operation": "list_issues", "arguments": {"include_archived": 1}},
        {"operation": "get_issue", "arguments": {"work_id": "safe-id"}, "request_id": "caller"},
        {"operation": "get_issue", "arguments": {"work_id": "safe-id"}, "actor_id": "caller"},
    ],
)
def test_unknown_or_nested_fields_and_non_strict_values_reject(payload: object) -> None:
    remote, client, _ = gateway()

    response = remote.dispatch(payload, credential="opaque-test-credential")

    assert response["reason_code"] == "invalid_remote_request"
    assert client.calls == []
    rendered = str(response) + repr(response)
    assert "unknown-operation" not in rendered
    assert "caller" not in rendered


@pytest.mark.parametrize(
    ("payload", "credential"),
    [
        ({"operation": "get_issue", "arguments": {"work_id": "UPPER"}}, "safe"),
        (
            {
                "operation": "create_issue",
                "arguments": {"work_id": "safe-id", "title": "x" * 513, "idempotency_key": "key"},
            },
            "safe",
        ),
        (
            {
                "operation": "create_issue",
                "arguments": {
                    "work_id": "safe-id",
                    "title": "line\nfeed",
                    "idempotency_key": "key",
                },
            },
            "safe",
        ),
        (
            {
                "operation": "transition_issue_to_review",
                "arguments": {"work_id": "safe-id", "idempotency_key": "x" * 257},
            },
            "safe",
        ),
        ({"operation": "get_issue", "arguments": {"work_id": "safe-id"}}, "x" * 4097),
        ({"operation": "get_issue", "arguments": {"work_id": "safe-id"}}, "bad\rcredential"),
    ],
)
def test_exact_bounds_and_control_characters_reject(payload: object, credential: str) -> None:
    remote, client, _ = gateway()

    response = remote.dispatch(payload, credential=credential)

    assert response["reason_code"] == "invalid_remote_request"
    assert client.calls == []


def test_invalid_request_id_factory_fails_closed_before_client() -> None:
    client = SpyClient()
    remote = RemoteGateway(
        client=client,
        request_id_factory=lambda: "caller-controlled",
        projector=RecordingProjector(),
    )

    response = remote.dispatch(
        {"operation": "get_issue", "arguments": {"work_id": "safe-id"}},
        credential="safe",
    )

    assert response["reason_code"] == "invalid_remote_request"
    assert client.calls == []


def test_remote_package_has_no_transport_or_authority_implementation_imports() -> None:
    package = Path(importlib.import_module("devgraph.remote").__file__).parent
    text = "\n".join(path.read_text() for path in package.glob("*.py"))

    for forbidden in (
        "matrix",
        "neo4j",
        "AuthorityContext",
        "LocalDevVerifier",
        "os.environ",
        "socket",
    ):
        assert forbidden not in text


@pytest.mark.parametrize(
    ("error", "reason_code"),
    [
        (DevgraphTimeout(), "upstream_timeout"),
        (DevgraphTransportError(), "upstream_unavailable"),
        (DevgraphMalformedProblem(), "malformed_upstream"),
        (DevgraphMalformedSuccess(), "malformed_upstream"),
        (DevgraphInvalidSuccessEnvelope(), "malformed_upstream"),
        (DevgraphUnexpectedContentType(), "malformed_upstream"),
        (
            DevgraphProblem(
                status=418,
                title="private-upstream-title",
                type="private:type",
                detail="private-upstream-detail",
                correlation_id="safe-correlation-1",
            ),
            "upstream_error",
        ),
    ],
)
def test_upstream_failures_are_classified_without_raw_echo(
    error: Exception,
    reason_code: str,
) -> None:
    class FailingClient:
        def get_issue(self, context: DevgraphRequestContext, **kwargs: Any) -> object:
            raise error

    gateway = RemoteGateway(
        client=FailingClient(),
        request_id_factory=lambda: "rmt_abcdefghijklmnopqrstuvwxyz",
        projector=TransportSafeProjector(),
    )

    response = gateway.dispatch(
        {"operation": "get_issue", "arguments": {"work_id": "safe-id"}},
        credential="safe-credential",
    )

    assert response.reason_code == reason_code
    rendered = response.model_dump_json() + repr(response)
    for private in (
        "private-upstream-title",
        "private-upstream-detail",
        "private:type",
        "safe-credential",
    ):
        assert private not in rendered
