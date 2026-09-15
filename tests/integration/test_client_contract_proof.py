from __future__ import annotations

import json

from client_contract_fixtures import build_client_contract_fixture

from devgraph.events.outbox import EMITTED_EVENT, EVENT_RECEIPT_LABEL


def test_bounded_create_duplicate_get_list_review_get_sequence() -> None:
    fixture = build_client_contract_fixture()
    client = fixture.client
    context = fixture.contexts.full_sequence
    create_key = "caller-create-key"
    status_key = "caller-status-key"

    created = client.create_issue(
        context,
        work_id="issue-client-contract-1",
        title="Bounded contract marker",
        idempotency_key=create_key,
    )
    duplicate = client.create_issue(
        context,
        work_id="issue-client-contract-1",
        title="Bounded contract marker",
        idempotency_key=create_key,
    )
    first_get = client.get_issue(context, work_id="issue-client-contract-1")
    listed = client.list_issues(context, include_archived=False)
    transitioned = client.transition_issue_to_review(
        context,
        work_id="issue-client-contract-1",
        idempotency_key=status_key,
    )
    final_get = client.get_issue(context, work_id="issue-client-contract-1")

    assert created.work is not None
    assert created.work.id == "issue-client-contract-1"
    assert created.work.status == "draft" and created.work.version == 1
    assert created.receipt.duplicate is False
    assert created.receipt.operation == "create_work_object"
    assert duplicate.work is None
    assert duplicate.receipt.duplicate is True
    assert duplicate.receipt.receipt_id == created.receipt.receipt_id
    assert first_get == created.work
    assert listed.items == (created.work,)
    assert transitioned.work is not None
    assert transitioned.work.status == "review" and transitioned.work.version == 2
    assert transitioned.receipt.duplicate is False
    assert transitioned.receipt.receipt_id != created.receipt.receipt_id
    assert transitioned.receipt.operation == "transition_work_object_status"
    assert final_get == transitioned.work

    observation = fixture.observation
    stored_issues = observation.storage.query("Issue")
    assert len(stored_issues) == 1
    assert stored_issues[0].id == "issue-client-contract-1"
    assert stored_issues[0].properties["status"] == "review"
    assert stored_issues[0].properties["version"] == 2

    receipt_nodes = observation.storage.query(EVENT_RECEIPT_LABEL)
    assert len(receipt_nodes) == 2
    assert {node.id for node in receipt_nodes} == {
        created.receipt.receipt_id,
        transitioned.receipt.receipt_id,
    }
    assert {node.properties["operation"] for node in receipt_nodes} == {
        "create_work_object",
        "transition_work_object_status",
    }
    for node in receipt_nodes:
        assert node.properties["subject_label"] == "Issue"
        assert node.properties["subject_id"] == "issue-client-contract-1"
        assert node.properties["status"] == "pending"
        digest = node.properties["idempotency_claim_digest"]
        assert isinstance(digest, str) and digest
        serialized = json.dumps(node.properties)
        assert create_key not in serialized and status_key not in serialized
        assert "Bounded contract marker" not in serialized

    emitted_edges = observation.storage.list_edges(EMITTED_EVENT)
    assert len(emitted_edges) == 2
    assert {edge.to_id for edge in emitted_edges} == {
        created.receipt.receipt_id,
        transitioned.receipt.receipt_id,
    }

    audit_pairs = [(record.category, record.operation) for record in observation.audit_log.records]
    assert audit_pairs == [
        ("write", "create_work_object"),
        ("read", "get_work_object"),
        ("read", "query_work_objects"),
        ("write", "transition_work_object_status"),
        ("read", "get_work_object"),
    ]
    public_receipts = json.dumps(
        [
            created.receipt.model_dump(),
            duplicate.receipt.model_dump(),
            transitioned.receipt.model_dump(),
        ]
    )
    assert create_key not in public_receipts and status_key not in public_receipts


def test_generic_client_proves_every_bounded_work_route_over_the_real_app() -> None:
    fixture = build_client_contract_fixture()
    client = fixture.client
    context = fixture.contexts.full_sequence

    initiative = client.create_work(
        context,
        kind="Initiative",
        work_id="initiative-parity",
        title="Parity initiative",
        idempotency_key="parity-create-initiative",
    )
    project = client.create_work(
        context,
        kind="Project",
        work_id="project-parity",
        title="Parity project",
        description="Project contract proof",
        priority=4,
        artifact_ids=("artifact-parity",),
        external_link_ids=("link-parity",),
        idempotency_key="parity-create-project",
    )
    child = client.create_work(
        context,
        kind="Issue",
        work_id="issue-child-parity",
        title="Child issue",
        idempotency_key="parity-create-child",
    )
    blocking = client.create_work(
        context,
        kind="Task",
        work_id="task-blocking-parity",
        title="Blocking task",
        idempotency_key="parity-create-blocking",
    )
    blocked = client.create_work(
        context,
        kind="Task",
        work_id="task-blocked-parity",
        title="Blocked task",
        idempotency_key="parity-create-blocked",
    )
    proposal = client.create_work(
        context,
        kind="Proposal",
        work_id="proposal-parity",
        title="Parity proposal",
        priority=7,
        idempotency_key="parity-create-proposal",
    )

    assert all(
        result.work is not None
        for result in (initiative, project, child, blocking, blocked, proposal)
    )
    fixture.observation.storage.create_edge(
        "Project",
        "project-parity",
        "HAS_CHILD",
        "Issue",
        "issue-child-parity",
    )
    fixture.observation.storage.create_edge(
        "Task",
        "task-blocking-parity",
        "BLOCKS",
        "Task",
        "task-blocked-parity",
    )

    patched = client.patch_work(
        context,
        kind="Project",
        work_id="project-parity",
        expected_version=1,
        title="Updated parity project",
        description="",
        priority=-1,
        artifact_ids=(),
        external_link_ids=("link-updated",),
        idempotency_key="parity-patch-project",
    )
    transitioned = client.transition_work_status(
        context,
        kind="Project",
        work_id="project-parity",
        status="review",
        idempotency_key="parity-status-project",
    )
    got = client.get_work(context, kind="Project", work_id="project-parity")
    listed = client.list_work(context, kind="Project")
    children = client.get_work_children(
        context,
        kind="Project",
        work_id="project-parity",
    )
    blockers = client.get_task_blockers(context, task_id="task-blocked-parity")
    archived = client.archive_work(
        context,
        kind="Initiative",
        work_id="initiative-parity",
        idempotency_key="parity-archive-initiative",
    )
    accepted = client.accept_proposal(
        context,
        proposal_id="proposal-parity",
        decision_id="decision-parity",
        decision_title="Accept parity proposal",
        idempotency_key="parity-accept-proposal",
    )
    converted = client.convert_proposal(
        context,
        proposal_id="proposal-parity",
        issue_id="issue-converted-parity",
        idempotency_key="parity-convert-proposal",
    )

    assert patched.work is not None
    assert patched.work.title == "Updated parity project"
    assert patched.work.description == ""
    assert patched.work.priority == -1
    assert patched.work.artifact_ids == ()
    assert patched.work.external_link_ids == ("link-updated",)
    assert transitioned.work is not None and transitioned.work.version == 3
    assert got == transitioned.work and listed.items == (got,)
    assert children.items == (child.work,)
    assert blockers.items == (blocking.work,)
    assert archived.work is not None and archived.work.status == "archived"
    assert accepted.work is not None and accepted.work.status == "accepted"
    assert converted.work is not None
    assert converted.work.kind == "Issue" and converted.work.priority == 7

    receipt_nodes = fixture.observation.storage.query(EVENT_RECEIPT_LABEL)
    assert len(receipt_nodes) == 11
    assert {node.properties["operation"] for node in receipt_nodes} == {
        "create_work_object",
        "update_work_object_content",
        "transition_work_object_status",
        "archive_work_object",
        "accept_proposal",
        "convert_accepted_proposal_to_issue",
    }
    serialized_receipts = json.dumps([node.properties for node in receipt_nodes])
    assert "parity-create-project" not in serialized_receipts
    assert context.credential not in serialized_receipts
