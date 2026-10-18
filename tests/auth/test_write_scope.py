"""Actual signed receiver requests; all identities, graphs and policies are fixtures."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from tests.auth.test_secs_work import KEY, NOW, canonical, config
from tests.auth.test_secs_work_v2 import proof_v2
from tests.model.test_arena_runtime import add_arena, assign
from tests.model.test_named_work import ref

from devgraph.arenas import ArenaMutations, ArenaRepository
from devgraph.auth import AuditLog
from devgraph.auth.access_policy import AccessDraft, compile_write
from devgraph.auth.secs_work import SecSWorkDenied
from devgraph.auth.secs_work_v2 import SecSWorkV2Verifier
from devgraph.auth.write_scope import (
    ReceiverWritePolicy,
    ScopedSecSWorkAdapter,
    require_write_scope_subset,
)
from devgraph.model.repository import WorkObjectVersionConflictError
from devgraph.named_work import NamedWorkMutations
from devgraph.progress import TODO_KINDS
from devgraph.storage.memory import MemoryGraphStorage
from devgraph.work_requests import WorkRequest

ACTOR = "pubkey:sha256:" + "a" * 64


def command(operation="patch", kind="Task", identifier="child", version=1, payload=None):
    return WorkRequest.from_json(canonical(dict(
        schema="devgraph.work-request.v2", operation=operation, kind=kind, id=identifier,
        expected_version=version, payload=payload if payload is not None else {"title": "Changed"},
    )))


def grant(operation="patch", kind="Task", ids=None, arenas=None):
    result = dict(
        operation="devgraph.work." + operation + ".v2",
        resources=[dict(kind=kind, **({"ids": ids} if ids is not None else {}))],
    )
    if arenas is not None:
        result["arena_ids"] = arenas
    return result


def policy(grants, **changes):
    value = dict(
        schema="devgraph.receiver-write-policy.v1", revision=1,
        stable_issuer="secs:test-work", audience="devgraph://receiver-local",
        native_policy_id="test-policy", native_policy_version=1,
        native_policy_digest_sha256="c" * 64,
        assignments=[dict(actor_id=ACTOR, not_before=NOW, not_after=NOW + 3600, grants=grants)],
    )
    value.update(changes)
    return ReceiverWritePolicy.model_validate(value)


def fixture(grants):
    storage, audit = MemoryGraphStorage(), AuditLog()
    state = [policy(grants)]
    adapter = ScopedSecSWorkAdapter(
        storage=storage, audit_log=audit, verifier=SecSWorkV2Verifier(config()),
        policy=lambda: state[0], clock=lambda: NOW,
    )
    return adapter, storage, audit, state


def execute(adapter, request, key=KEY):
    return adapter.execute(
        request_json=request.canonical, projection_json=proof_v2(request.canonical, key),
        idempotency_key=key,
    )


def create(storage, kind, identifier):
    return NamedWorkMutations(storage).execute(command(
        "create", kind, identifier, None, dict(id=identifier, title="Fixture")
    ))


def hierarchy(storage):
    arenas = ArenaMutations(storage)
    add_arena(arenas, "a")
    add_arena(arenas, "b")
    for kind, identifier in [("Initiative", "root-a"), ("Initiative", "root-b"),
                             ("Project", "project-a"), ("Project", "project-b"),
                             ("Task", "child")]:
        create(storage, kind, identifier)
    assign(arenas, "Initiative", "root-a", 1, arena=ref("Arena", "a", 1))
    assign(arenas, "Initiative", "root-b", 1, arena=ref("Arena", "b", 1))
    for source, child_kind, child in [
        ("root-a", "Project", "project-a"), ("root-b", "Project", "project-b")
    ]:
        storage.create_edge("Initiative", source, "HAS_CHILD", child_kind, child)
    storage.create_edge("Project", "project-a", "HAS_CHILD", "Task", "child")


@pytest.mark.parametrize("kind", TODO_KINDS)
def test_all_six_types_accept_only_explicit_ids_and_preserve_versions(kind):
    adapter, storage, audit, _ = fixture([grant(kind=kind, ids=["one"])])
    create(storage, kind, "one")
    create(storage, kind, "two")
    result, receipt, duplicate = execute(adapter, command(kind=kind, identifier="one"))
    assert result.version == 2 and result.title == "Changed" and not duplicate
    assert audit.records[-1].safe_summary["write_scope_policy_revision"] == 1
    _, again, duplicate = execute(adapter, command(kind=kind, identifier="one"))
    assert duplicate and again.id == receipt.id
    with pytest.raises(WorkObjectVersionConflictError):
        execute(adapter, command(kind=kind, identifier="one"), KEY + "-stale")
    with pytest.raises(SecSWorkDenied):
        execute(adapter, command(kind=kind, identifier="two"), KEY + "-other")
    assert NamedWorkMutations(storage).repository.get_by_id(kind, "two").version == 1


def test_inherited_arena_intersects_id_and_operation_without_cross_product():
    adapter, storage, audit, _ = fixture([
        grant(ids=["child"], arenas=["a"]),
        grant(operation="archive", ids=["child"], arenas=["b"]),
    ])
    hierarchy(storage)
    execute(adapter, command())
    with pytest.raises(SecSWorkDenied):
        execute(adapter, command("archive", version=2, payload={}), KEY + "-archive")
    assert len(audit.records) == 1


@pytest.mark.parametrize("arenas", [["b"], ["a"]])
def test_missing_or_outside_membership_never_matches_an_arena(arenas):
    adapter, storage, audit, _ = fixture([grant(arenas=arenas)])
    create(storage, "Task", "child")
    before = storage.query()
    with pytest.raises(SecSWorkDenied):
        execute(adapter, command())
    assert storage.query() == before and audit.records == []


@pytest.mark.parametrize("corruption", ["duplicate", "two-parents", "cycle"])
def test_ambiguous_inherited_membership_is_denied(corruption):
    adapter, storage, audit, _ = fixture([grant(arenas=["a"])])
    hierarchy(storage)
    if corruption == "duplicate":
        storage._edges.append(next(e for e in storage.list_edges("HAS_CHILD")
                                   if e.to_id == "child"))
    elif corruption == "two-parents":
        storage.create_edge("Project", "project-b", "HAS_CHILD", "Task", "child")
    else:
        storage.create_edge("Task", "child", "HAS_CHILD", "Initiative", "root-a")
    with pytest.raises(SecSWorkDenied):
        execute(adapter, command())
    assert audit.records == []


@pytest.mark.parametrize("selector", ["single", "separate", "both"])
def test_reparent_requires_one_grant_covering_before_and_after_and_rolls_back(selector):
    child_grants = [grant("parent.set", ids=["child"], arenas=["a"])]
    if selector == "separate":
        child_grants.append(grant("parent.set", ids=["child"], arenas=["b"]))
    elif selector == "both":
        child_grants = [grant("parent.set", ids=["child"], arenas=["a", "b"])]
    adapter, storage, audit, _ = fixture(child_grants + [
        grant("parent.set", "Project", ["project-a", "project-b"])
    ])
    hierarchy(storage)
    before = (storage.query(), storage.list_edges())
    request = command("parent.set", payload=dict(
        previous_parent=ref("Project", "project-a", 1),
        parent=ref("Project", "project-b", 1),
    ))
    if selector == "both":
        execute(adapter, request)
        assert ArenaRepository(storage).membership("Task", "child").arena.id == "b"
    else:
        with pytest.raises(SecSWorkDenied, match="write_resource_scope_changed"):
            execute(adapter, request)
        assert before == (storage.query(), storage.list_edges())
        assert audit.records == []


def test_detach_cannot_escape_arena_restriction():
    adapter, storage, audit, _ = fixture([
        grant("parent.set", ids=["child"], arenas=["a"]),
        grant("parent.set", "Project", ["project-a"]),
    ])
    hierarchy(storage)
    with pytest.raises(SecSWorkDenied, match="write_resource_scope_changed"):
        execute(adapter, command("parent.set", payload=dict(
            previous_parent=ref("Project", "project-a", 1), parent=None,
        )))
    assert ArenaRepository(storage).membership("Task", "child").arena.id == "a"
    assert audit.records == []


def test_relationship_requires_both_endpoints_and_same_operation():
    adapter, storage, audit, _ = fixture([
        grant("dependency.add", ids=["child"], arenas=["a"]),
        grant("patch", "Project", ["project-b"]),
    ])
    hierarchy(storage)
    with pytest.raises(SecSWorkDenied):
        execute(adapter, command("dependency.add", payload=dict(
            target=ref("Project", "project-b", 1)
        )))
    assert storage.list_edges("DEPENDS_ON") == [] and audit.records == []


def test_base_todo_completion_requires_explicit_evidence_resource_and_keeps_simple_workflow():
    grants = [grant("progress.set", "Todo", ["simple"])]
    adapter, storage, audit, state = fixture(grants)
    create(storage, "Todo", "simple")
    request = command("progress.set", "Todo", "simple", payload=dict(
        progress="done", reason="Finished the call", record_id="call-receipt"
    ))
    with pytest.raises(SecSWorkDenied):
        execute(adapter, request)
    assert storage.get_node("ReviewPacket", "call-receipt") is None
    state[0] = policy(grants + [grant("progress.set", "ReviewPacket", ["call-receipt"])])
    work, _, _ = execute(adapter, request)
    assert work.progress.value == "done" and work.workflow_json is None
    assert storage.get_node("ReviewPacket", "call-receipt") is not None
    assert len(audit.records) == 1


def test_archive_restore_keeps_stage_and_progress_independent():
    adapter, storage, _, _ = fixture([grant("archive"), grant("restore")])
    hierarchy(storage)
    before = NamedWorkMutations(storage).repository.get_by_id("Task", "child")
    archived, _, _ = execute(adapter, command("archive", payload={}))
    restored, _, _ = execute(adapter, command("restore", version=2, payload={}), KEY + "-restore")
    assert archived.archived and not restored.archived
    assert before.progress == archived.progress == restored.progress
    assert before.workflow_json == archived.workflow_json == restored.workflow_json


def test_revocation_while_queued_denies_without_receipt_or_audit():
    adapter, storage, audit, state = fixture([grant()])
    create(storage, "Task", "child")
    original = storage.work_mutation_transaction

    @contextmanager
    def waiting():
        with original():
            state[0] = policy([], assignments=[])
            yield

    storage.work_mutation_transaction = waiting
    with pytest.raises(SecSWorkDenied, match="write_scope_not_current"):
        execute(adapter, command())
    assert storage.query("EventReceipt") == [] and audit.records == []
    assert storage.get_node("Task", "child").properties["version"] == 1


@pytest.mark.parametrize("change", ["missing", "expired", "actor", "native-binding"])
def test_current_policy_is_required_even_for_an_idempotent_retry(change):
    adapter, storage, _, state = fixture([grant()])
    create(storage, "Task", "child")
    execute(adapter, command())
    value = state[0].model_dump(mode="json", by_alias=True, exclude_none=True)
    if change == "missing":
        state[0] = None
    else:
        if change == "expired":
            value["assignments"][0]["not_before"] = NOW - 1
            value["assignments"][0]["not_after"] = NOW
        elif change == "actor":
            value["assignments"][0]["actor_id"] = "pubkey:sha256:" + "b" * 64
        else:
            value["native_policy_digest_sha256"] = "d" * 64
        state[0] = ReceiverWritePolicy.model_validate(value)
    with pytest.raises(SecSWorkDenied):
        execute(adapter, command())
    assert len(storage.query("EventReceipt")) == 1


def test_bad_native_signature_never_reaches_scope_policy():
    adapter, storage, _, _ = fixture([grant()])
    create(storage, "Task", "child")
    adapter.policy = lambda: pytest.fail("policy accessed before native verification")
    proof = json.loads(proof_v2(command().canonical))
    proof["secs_verifier_signature"] = "A" * 86
    with pytest.raises(SecSWorkDenied):
        adapter.execute(request_json=command().canonical, projection_json=canonical(proof),
                        idempotency_key=KEY)


def test_attenuation_cannot_remove_arena_filter_or_cross_independent_grants():
    parent = policy([grant(arenas=["a"]), grant("archive", arenas=["b"])]).assignments[0]
    child = policy([grant(ids=["child"], arenas=["a"])]).assignments[0]
    require_write_scope_subset(child, parent)
    for selected in [grant(), grant(arenas=["b"]), grant("archive", arenas=["a"])]:
        with pytest.raises(ValueError):
            require_write_scope_subset(policy([selected]).assignments[0], parent)


@pytest.mark.parametrize("change", ["empty", "duplicate", "unknown", "provenance", "bool"])
def test_closed_policy_rejects_ambiguous_or_unsupported_selectors(change):
    value = policy([grant(arenas=["a"])]).model_dump(mode="json", by_alias=True, exclude_none=True)
    selected = value["assignments"][0]["grants"][0]
    if change == "empty":
        selected["arena_ids"] = []
    elif change == "duplicate":
        selected["arena_ids"] = ["a", "a"]
    elif change == "unknown":
        selected["delegate"] = True
    elif change == "provenance":
        selected["resources"] = [{"kind": "Decision"}]
    else:
        value["assignments"][0]["not_before"] = True
    with pytest.raises(ValidationError):
        ReceiverWritePolicy.model_validate(value)


def test_historical_draft_rejects_arena_and_export_never_flattens_restrictions():
    from tests.auth.test_access_policy import draft

    value = draft().model_dump(mode="json", by_alias=True, exclude_none=True)
    value["write_grants"] = [grant(arenas=["a"])]
    with pytest.raises(ValidationError):
        AccessDraft.model_validate(value)
    unsafe = SimpleNamespace(write_grants=policy([grant(arenas=["a"])]).assignments[0].grants)
    with pytest.raises(ValueError, match="cannot flatten"):
        compile_write(unsafe, policy_id="test", policy_version=1)


def test_scoped_policy_schema_and_example_are_current():
    from pathlib import Path

    root = Path(__file__).parents[2] / "docs/contracts/receiver-write-policy/v1"
    assert json.loads((root / "schema.json").read_text()) == ReceiverWritePolicy.model_json_schema()
    selected = ReceiverWritePolicy.model_validate_json((root / "example.json").read_text())
    assert selected.assignments[0].grants[0].arena_ids == ("demo",)


def test_status_recovers_only_original_receipt_and_unknown_does_not_dispatch():
    adapter, storage, audit, state = fixture([grant()])
    create(storage, "Task", "child")
    request = command()
    arguments = dict(request_json=request.canonical, projection_json=proof_v2(request.canonical),
                     idempotency_key=KEY)
    before = (storage.query(), storage.list_edges())
    assert adapter.status(**arguments) is None
    assert before == (storage.query(), storage.list_edges())
    assert audit.records[-1].safe_summary["outcome"] == "unknown"
    _, receipt, _ = execute(adapter, request)
    assert adapter.status(**arguments).id == receipt.id
    assert len(storage.query("EventReceipt")) == 1
    assert audit.records[-1].safe_summary["status_only"] is True
    state[0] = policy([], assignments=[])
    with pytest.raises(SecSWorkDenied):
        adapter.status(**arguments)


def test_proposal_disposition_needs_decision_grant_and_never_completes_delivery():
    adapter, storage, _, state = fixture([grant("proposal.reject", "Proposal", ["idea"])])
    create(storage, "Proposal", "idea")
    create(storage, "Issue", "delivery")
    request = command("proposal.reject", "Proposal", "idea", payload=dict(
        decision_id="rejection", reason="Not proceeding"
    ))
    with pytest.raises(SecSWorkDenied):
        execute(adapter, request)
    assert storage.get_node("Decision", "rejection") is None
    state[0] = policy([
        grant("proposal.reject", "Proposal", ["idea"]),
        grant("proposal.reject", "Decision", ["rejection"]),
    ])
    execute(adapter, request)
    assert storage.get_node("Decision", "rejection") is not None
    assert storage.get_node("Issue", "delivery").properties["progress"] == "not_started"


def test_write_grant_does_not_bypass_subtype_completion_gates():
    from devgraph.workflows import WorkflowConflict

    adapter, storage, audit, _ = fixture([grant("workflow.transition")])
    create(storage, "Task", "child")
    with pytest.raises(WorkflowConflict):
        execute(adapter, command("workflow.transition", payload=dict(stage="done")))
    assert audit.records == [] and storage.query("EventReceipt") == []


def test_new_root_cannot_be_created_using_only_an_arena_grant():
    adapter, storage, _, _ = fixture([grant("create", arenas=["a"])])
    add_arena(ArenaMutations(storage), "a")
    with pytest.raises(SecSWorkDenied):
        execute(adapter, command("create", version=None, payload=dict(id="child", title="New")))
    assert storage.get_node("Task", "child") is None


def test_canonical_base_todo_cannot_acquire_arena_authority_through_invalid_membership():
    adapter, storage, _, _ = fixture([grant(kind="Todo", arenas=["a"])])
    add_arena(ArenaMutations(storage), "a")
    create(storage, "Todo", "child")
    storage.create_edge("Arena", "a", "CONTAINS_WORK", "Todo", "child")
    with pytest.raises(SecSWorkDenied):
        execute(adapter, command(kind="Todo"))


def test_inherited_scope_does_not_scan_unrelated_work(monkeypatch):
    adapter, storage, _, _ = fixture([grant(arenas=["a"])])
    hierarchy(storage)
    # This assertion covers scope resolution itself, independently of outbox reads.
    for index in range(1500):
        create(storage, "Task", f"unrelated-{index}")
    calls = []
    original = storage.work_containment

    def tracked(kind, identifier):
        calls.append((kind, identifier))
        return original(kind, identifier)

    monkeypatch.setattr(storage, "work_containment", tracked)
    execute(adapter, command())
    assert len(calls) == 6  # one three-record chain, before and after
    assert {identifier for _, identifier in calls} == {"child", "project-a", "root-a"}


def test_arena_membership_move_checks_prior_and_destination_and_rolls_back():
    from tests.auth.test_secs_work import SEED, b64
    from tests.model.test_arena_runtime import arena_command

    from devgraph.auth.secs_work_v2 import SIGNATURE_DOMAIN
    from devgraph.credential_requests import credential_request_binding

    operation = "devgraph.arena.member.set.v1"
    adapter, storage, audit, _ = fixture([
        dict(operation=operation, resources=[dict(kind="Task", ids=["child"])], arena_ids=["a"]),
        dict(operation=operation, resources=[dict(kind="Arena", ids=["a", "b"])])
    ])
    arenas = ArenaMutations(storage)
    add_arena(arenas, "a")
    add_arena(arenas, "b")
    create(storage, "Task", "child")
    assign(arenas, "Task", "child", 1, arena=ref("Arena", "a", 1))
    request = arena_command("member.set", "Task", "child", dict(
        previous_arena=ref("Arena", "a", 2), arena=ref("Arena", "b", 1)
    ), 2)
    projection = json.loads(proof_v2(command().canonical))
    projection.pop("secs_verifier_signature")
    binding = credential_request_binding(request.canonical, KEY)
    projection.update(operation=operation, resources=list(request.resources),
                      request_digest_sha256=request.digest,
                      credential_request_digest_sha256=binding.request_digest_sha256,
                      disclosure_digest_sha256=binding.disclosure_digest_sha256)
    projection["secs_verifier_signature"] = b64(SEED.sign(SIGNATURE_DOMAIN + canonical(projection)))
    before = (storage.query(), storage.list_edges())
    with pytest.raises(SecSWorkDenied, match="write_resource_scope_changed"):
        adapter.execute(request_json=request.canonical, projection_json=canonical(projection),
                        idempotency_key=KEY)
    assert before == (storage.query(), storage.list_edges()) and audit.records == []


def test_status_never_returns_receipt_for_changed_request_bytes():
    from devgraph.events.outbox import IdempotencyScopeConflict

    adapter, storage, _, _ = fixture([grant()])
    create(storage, "Task", "child")
    execute(adapter, command())
    changed = command(payload={"title": "Different"})
    with pytest.raises(IdempotencyScopeConflict):
        adapter.status(request_json=changed.canonical, projection_json=proof_v2(changed.canonical),
                       idempotency_key=KEY)
    assert len(storage.query("EventReceipt")) == 1


def test_denied_read_credential_cannot_substitute_for_native_authority():
    adapter, storage, _, _ = fixture([grant()])
    create(storage, "Task", "child")
    with pytest.raises(SecSWorkDenied):
        adapter.execute(request_json=command().canonical,
                        projection_json=b'{"scope":"devgraph.read"}', idempotency_key=KEY)
    assert storage.query("EventReceipt") == []
