from __future__ import annotations

from typing import Any

import pytest

from devgraph.client import (
    DevgraphHttpClient,
    DevgraphInvalidSuccessEnvelope,
    DevgraphRequestContext,
    MutationResult,
)


class Response:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self._body = body

    def json(self) -> Any:
        return self._body


class RecordingTransport:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def work(
    kind: str,
    work_id: str,
    *,
    status: str = "draft",
    version: int = 1,
) -> dict[str, Any]:
    return {
        "id": work_id,
        "kind": kind,
        "title": f"{kind} title",
        "description": "",
        "status": status,
        "version": version,
        "priority": 0,
        "artifact_ids": [],
        "external_link_ids": [],
    }


def receipt(
    operation: str,
    label: str,
    subject_id: str,
) -> dict[str, Any]:
    return {
        "receipt_id": f"receipt-{operation}",
        "operation": operation,
        "subject_label": label,
        "subject_id": subject_id,
        "receipt_status": "pending",
        "duplicate": False,
        "correlation_id": "corr-parity",
    }


def mutation(
    operation: str,
    label: str,
    subject_id: str,
    projected_work: dict[str, Any],
) -> dict[str, Any]:
    return {
        "work": projected_work,
        "receipt": receipt(operation, label, subject_id),
    }


def test_generic_methods_forward_the_complete_bounded_work_contract() -> None:
    responses = [
        Response(
            201,
            mutation(
                "create_work_object",
                "Project",
                "project-1",
                work("Project", "project-1"),
            ),
        ),
        Response(200, work("Project", "project-1")),
        Response(200, {"items": [work("Project", "project-1")]}),
        Response(
            200,
            mutation(
                "update_work_object_content",
                "Project",
                "project-1",
                work("Project", "project-1", version=2),
            ),
        ),
        Response(
            200,
            mutation(
                "transition_work_object_status",
                "Project",
                "project-1",
                work("Project", "project-1", status="review", version=3),
            ),
        ),
        Response(
            200,
            mutation(
                "archive_work_object",
                "Project",
                "project-1",
                work("Project", "project-1", status="archived", version=4),
            ),
        ),
        Response(200, [work("Issue", "issue-child")]),
        Response(200, [work("Task", "task-blocker")]),
        Response(
            200,
            mutation(
                "accept_proposal",
                "Proposal",
                "proposal-1",
                work("Proposal", "proposal-1", status="accepted", version=2),
            ),
        ),
        Response(
            200,
            mutation(
                "convert_accepted_proposal_to_issue",
                "Issue",
                "issue-converted",
                work("Issue", "issue-converted"),
            ),
        ),
    ]
    transport = RecordingTransport(responses)
    client = DevgraphHttpClient(
        transport=transport,
        base_url="http://in-process",
        timeout=2.5,
    )
    context = DevgraphRequestContext(credential="opaque-parity-marker")

    created = client.create_work(
        context,
        kind="Project",
        work_id="project-1",
        title="Project title",
        description="Project description",
        priority=3,
        artifact_ids=("artifact-1",),
        external_link_ids=("link-1",),
        idempotency_key="create-key",
    )
    got = client.get_work(context, kind="Project", work_id="project-1")
    listed = client.list_work(context, kind="Project", include_archived=True)
    patched = client.patch_work(
        context,
        kind="Project",
        work_id="project-1",
        expected_version=1,
        title="Updated",
        description="",
        priority=-2,
        artifact_ids=(),
        external_link_ids=("link-2",),
        idempotency_key="patch-key",
    )
    transitioned = client.transition_work_status(
        context,
        kind="Project",
        work_id="project-1",
        status="review",
        idempotency_key="status-key",
    )
    archived = client.archive_work(
        context,
        kind="Project",
        work_id="project-1",
        idempotency_key="archive-key",
    )
    children = client.get_work_children(
        context,
        kind="Project",
        work_id="project-1",
    )
    blockers = client.get_task_blockers(context, task_id="task-blocked")
    accepted = client.accept_proposal(
        context,
        proposal_id="proposal-1",
        decision_id="decision-1",
        decision_title="Accept",
        idempotency_key="accept-key",
    )
    converted = client.convert_proposal(
        context,
        proposal_id="proposal-1",
        issue_id="issue-converted",
        idempotency_key="convert-key",
    )

    assert all(
        isinstance(result, MutationResult)
        for result in (created, patched, transitioned, archived, accepted, converted)
    )
    assert got.kind == "Project" and listed.items == (got,)
    assert children.items[0].kind == "Issue"
    assert blockers.items[0].kind == "Task"
    assert [call["method"] for call in transport.calls] == [
        "POST",
        "GET",
        "GET",
        "PATCH",
        "POST",
        "POST",
        "GET",
        "GET",
        "POST",
        "POST",
    ]
    assert [call["url"] for call in transport.calls] == [
        "http://in-process/work/Project",
        "http://in-process/work/Project/project-1",
        "http://in-process/work/Project?include_archived=true",
        "http://in-process/work/Project/project-1",
        "http://in-process/work/Project/project-1/status",
        "http://in-process/work/Project/project-1/archive",
        "http://in-process/work/Project/project-1/children",
        "http://in-process/tasks/task-blocked/blockers",
        "http://in-process/proposals/proposal-1/accept",
        "http://in-process/proposals/proposal-1/convert",
    ]
    assert transport.calls[0]["json"] == {
        "id": "project-1",
        "title": "Project title",
        "description": "Project description",
        "priority": 3,
        "artifact_ids": ["artifact-1"],
        "external_link_ids": ["link-1"],
    }
    assert transport.calls[3]["headers"] == {
        "Authorization": "Bearer opaque-parity-marker",
        "Idempotency-Key": "patch-key",
        "If-Match": '"1"',
    }
    assert transport.calls[3]["json"] == {
        "title": "Updated",
        "description": "",
        "priority": -2,
        "artifact_ids": [],
        "external_link_ids": ["link-2"],
    }
    assert transport.calls[4]["json"] == {"status": "review"}
    assert "json" not in transport.calls[5]
    assert transport.calls[8]["json"] == {
        "decision_id": "decision-1",
        "decision_title": "Accept",
    }
    assert transport.calls[9]["json"] == {"issue_id": "issue-converted"}
    assert all(call["timeout"] == 2.5 for call in transport.calls)


@pytest.mark.parametrize("kind", ["Decision", "issue", "", 3])
def test_generic_methods_reject_unknown_work_kinds_without_transport(kind: object) -> None:
    transport = RecordingTransport([])
    client = DevgraphHttpClient(transport=transport, base_url="http://x", timeout=1)

    with pytest.raises(ValueError, match="unsupported work kind"):
        client.get_work(
            DevgraphRequestContext(credential="opaque"),
            kind=kind,  # type: ignore[arg-type]
            work_id="x",
        )

    assert transport.calls == []


def test_generic_input_preconditions_fail_before_transport() -> None:
    transport = RecordingTransport([])
    client = DevgraphHttpClient(transport=transport, base_url="http://x", timeout=1)
    context = DevgraphRequestContext(credential="opaque")

    with pytest.raises(ValueError, match="include_archived must be boolean"):
        client.list_work(
            context,
            kind="Issue",
            include_archived="false",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="unsupported work status"):
        client.transition_work_status(
            context,
            kind="Issue",
            work_id="i",
            status="closed",  # type: ignore[arg-type]
            idempotency_key="k",
        )
    with pytest.raises(ValueError, match="positive integer"):
        client.patch_work(
            context,
            kind="Issue",
            work_id="i",
            expected_version=True,  # type: ignore[arg-type]
            title="x",
            idempotency_key="k",
        )
    with pytest.raises(ValueError, match="at least one field"):
        client.patch_work(
            context,
            kind="Issue",
            work_id="i",
            expected_version=1,
            idempotency_key="k",
        )
    with pytest.raises(ValueError, match="sequence of strings"):
        client.create_work(
            context,
            kind="Issue",
            work_id="i",
            title="x",
            artifact_ids="not-a-sequence",  # type: ignore[arg-type]
            idempotency_key="k",
        )

    assert transport.calls == []


@pytest.mark.parametrize(
    "body",
    [
        mutation(
            "archive_work_object",
            "Issue",
            "issue-1",
            work("Issue", "issue-1"),
        ),
        mutation(
            "create_work_object",
            "Project",
            "issue-1",
            work("Issue", "issue-1"),
        ),
        mutation(
            "create_work_object",
            "Issue",
            "other-issue",
            work("Issue", "issue-1"),
        ),
        mutation(
            "create_work_object",
            "Issue",
            "issue-1",
            work("Project", "issue-1"),
        ),
        mutation(
            "create_work_object",
            "Issue",
            "issue-1",
            work("Issue", "other-issue"),
        ),
        mutation(
            "create_work_object",
            "Issue",
            "issue-1",
            work("Issue", "issue-1", status="review", version=2),
        ),
        {
            "work": None,
            "receipt": receipt("create_work_object", "Issue", "issue-1"),
        },
    ],
)
def test_mutation_receipt_and_projection_mismatches_fail_closed(body: dict[str, Any]) -> None:
    client = DevgraphHttpClient(
        transport=RecordingTransport([Response(201, body)]),
        base_url="http://in-process",
        timeout=1,
    )

    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client.create_work(
            DevgraphRequestContext(credential="opaque"),
            kind="Issue",
            work_id="issue-1",
            title="Issue title",
            idempotency_key="create-key",
        )


def test_kind_mismatches_in_reads_and_task_blockers_fail_closed() -> None:
    transport = RecordingTransport(
        [
            Response(200, work("Project", "issue-1")),
            Response(200, {"items": [work("Project", "issue-1")]}),
            Response(200, [work("Issue", "not-a-task")]),
        ]
    )
    client = DevgraphHttpClient(
        transport=transport,
        base_url="http://in-process",
        timeout=1,
    )
    context = DevgraphRequestContext(credential="opaque")

    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client.get_work(context, kind="Issue", work_id="issue-1")
    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client.list_work(context, kind="Issue")
    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client.get_task_blockers(context, task_id="blocked")


def test_operation_specific_work_id_and_status_mismatches_fail_closed() -> None:
    transport = RecordingTransport(
        [
            Response(
                200,
                mutation(
                    "transition_work_object_status",
                    "Issue",
                    "issue-1",
                    work("Issue", "issue-1", status="draft"),
                ),
            ),
            Response(
                200,
                mutation(
                    "archive_work_object",
                    "Issue",
                    "issue-1",
                    work("Issue", "issue-1", status="review", version=2),
                ),
            ),
            Response(
                200,
                mutation(
                    "accept_proposal",
                    "Proposal",
                    "proposal-1",
                    work("Proposal", "other-proposal", status="accepted", version=2),
                ),
            ),
            Response(
                200,
                mutation(
                    "convert_accepted_proposal_to_issue",
                    "Issue",
                    "issue-converted",
                    work("Issue", "other-issue"),
                ),
            ),
        ]
    )
    client = DevgraphHttpClient(
        transport=transport,
        base_url="http://in-process",
        timeout=1,
    )
    context = DevgraphRequestContext(credential="opaque")

    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client.transition_work_status(
            context,
            kind="Issue",
            work_id="issue-1",
            status="review",
            idempotency_key="status-key",
        )
    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client.archive_work(
            context,
            kind="Issue",
            work_id="issue-1",
            idempotency_key="archive-key",
        )
    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client.accept_proposal(
            context,
            proposal_id="proposal-1",
            decision_id="decision-1",
            decision_title="Accept",
            idempotency_key="accept-key",
        )
    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client.convert_proposal(
            context,
            proposal_id="proposal-1",
            issue_id="issue-converted",
            idempotency_key="convert-key",
        )

    assert len(transport.calls) == 4
