"""Commit 2: request/response envelope validation.

Strict request models reject unknown fields, so credential material or
raw payload blobs cannot ride along in a body. No schema carries an
idempotency-key or credential field. All fixtures are synthetic.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from devgraph.api.schemas import (
    AcceptProposalRequest,
    ConvertProposalRequest,
    CreateWorkObjectRequest,
    ExportByIdsRequest,
    MonitorWorkProgressEnvelope,
    MutationReceiptEnvelope,
    ProblemDetail,
    StatusTransitionRequest,
    UpdateWorkObjectRequest,
    WorkObjectEnvelope,
    parse_if_match,
)


class TestStrictRequests:
    def test_accept_request_validates(self) -> None:
        request = AcceptProposalRequest(decision_id="d-1", decision_title="Approve")
        assert request.decision_id == "d-1"

    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AcceptProposalRequest(
                decision_id="d-1",
                decision_title="Approve",
                credential="FAKE-SECRET-smuggle",
            )
        with pytest.raises(ValidationError):
            ConvertProposalRequest(issue_id="i-1", raw_payload="blob")

    def test_empty_identifiers_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AcceptProposalRequest(decision_id="", decision_title="Approve")
        with pytest.raises(ValidationError):
            ConvertProposalRequest(issue_id="")


class TestNoSensitiveFields:
    @pytest.mark.parametrize(
        "model",
        [
            AcceptProposalRequest,
            ConvertProposalRequest,
            WorkObjectEnvelope,
            MutationReceiptEnvelope,
            ProblemDetail,
        ],
    )
    def test_no_credential_or_key_fields(self, model) -> None:
        names = set(model.model_fields)
        for forbidden in ("credential", "idempotency_key", "token", "secret"):
            assert forbidden not in names, (model.__name__, forbidden)


class TestProblemDetail:
    def test_defaults_are_rfc7807_compatible(self) -> None:
        problem = ProblemDetail(title="Forbidden", status=403)
        payload = problem.model_dump()
        assert payload["type"] == "about:blank"
        assert payload["status"] == 403
        assert payload["correlation_id"] is None


def test_completion_requests_are_strict_and_apply_defaults() -> None:
    created = CreateWorkObjectRequest(id="i-1", title="Title")
    assert created.model_dump() == {
        "id": "i-1",
        "title": "Title",
        "description": "",
        "priority": 0,
        "artifact_ids": [],
        "external_link_ids": [],
    }
    assert StatusTransitionRequest(status="review").status == "review"
    assert ExportByIdsRequest(kind="Issue", ids=[]).ids == []
    with pytest.raises(ValidationError):
        CreateWorkObjectRequest(id="i-1", title="Title", status="draft")
    with pytest.raises(ValidationError):
        UpdateWorkObjectRequest()


@pytest.mark.parametrize("value", ["a", "a--b", "a" * 256])
def test_request_identifiers_use_shared_canonical_grammar(value: str) -> None:
    assert CreateWorkObjectRequest(id=value, title="Title").id == value
    assert ExportByIdsRequest(kind="Issue", ids=[value]).ids == [value]


@pytest.mark.parametrize("value", ["BAD", "bad_underscore", "bad space", "é", "a" * 257])
def test_request_identifiers_reject_noncanonical_values(value: str) -> None:
    with pytest.raises(ValidationError):
        CreateWorkObjectRequest(id=value, title="Title")
    with pytest.raises(ValidationError):
        ExportByIdsRequest(kind="Issue", ids=[value])
    with pytest.raises(ValidationError):
        CreateWorkObjectRequest(id="i-1", title="Title", artifact_ids=[value])
    with pytest.raises(ValidationError):
        UpdateWorkObjectRequest(external_link_ids=[value])


@pytest.mark.parametrize("value", [True, 1.0, "1", 9223372036854775808, -9223372036854775809])
def test_request_priority_rejects_coercion_and_signed_overflow(value: object) -> None:
    with pytest.raises(ValidationError):
        CreateWorkObjectRequest(id="issue-1", title="Title", priority=value)


@pytest.mark.parametrize(
    "value, expected",
    [('"1"', 1), ('"42"', 42), ('"9223372036854775807"', 9223372036854775807)],
)
def test_if_match_accepts_only_quoted_positive_integer(value: str, expected: int) -> None:
    assert parse_if_match(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "1",
        'W/"1"',
        '"0"',
        '"-1"',
        '"1", "2"',
        ' "1"',
        '"01"',
        '"9223372036854775808"',
    ],
)
def test_if_match_rejects_every_other_grammar(value: str) -> None:
    with pytest.raises(ValueError, match="Invalid version precondition"):
        parse_if_match(value)


@pytest.mark.parametrize(
    "field", ["title", "description", "priority", "artifact_ids", "external_link_ids"]
)
def test_patch_explicit_null_is_rejected_while_omission_is_preserved(field: str) -> None:
    with pytest.raises(ValidationError):
        UpdateWorkObjectRequest.model_validate({field: None})
    assert UpdateWorkObjectRequest(title="ok").model_dump(exclude_unset=True) == {"title": "ok"}


@pytest.mark.parametrize("status", ["draft", "review", "accepted", "archived"])
def test_status_schema_accepts_exact_work_status_values(status: str) -> None:
    assert StatusTransitionRequest(status=status).status == status


def test_status_schema_rejects_nonexistent_completed() -> None:
    with pytest.raises(ValidationError):
        StatusTransitionRequest(status="completed")


def test_monitor_progress_schema_enforces_canonical_terminal_math() -> None:
    progress = MonitorWorkProgressEnvelope(
        schema_version="devgraph.work-progress.v0",
        basis="leaf_work",
        completed=2,
        total=3,
        percent=67,
        status_counts={"draft": 1, "review": 0, "accepted": 1, "archived": 1},
    )
    assert progress.percent == 67
    with pytest.raises(ValidationError):
        MonitorWorkProgressEnvelope(
            schema_version="devgraph.work-progress.v0",
            basis="leaf_work",
            completed=1,
            total=3,
            percent=34,
            status_counts={"draft": 1, "review": 0, "accepted": 1, "archived": 1},
        )
