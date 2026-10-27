"""Explicit dynamic collection scope; legacy fixed-ID contracts stay closed."""

import json

import pytest
from tests.model.test_arena_runtime import add_arena
from tests.native_read.test_arena_access import arena_draft

from devgraph.arenas import ArenaMutations
from devgraph.auth.access_policy import require_subset
from devgraph.auth.arena_access import (
    ArenaAccessDraft,
    CollectionArenaAccessDraft,
    CollectionArenaReadGrant,
)
from devgraph.auth.delegated_contract import ArenaReadGrant, canonical_json
from devgraph.auth.errors import ForbiddenError
from devgraph.auth.monitor_read import project_monitor_read
from devgraph.storage.memory import MemoryGraphStorage


def test_dynamic_member_selector_includes_arenas_created_after_grant():
    storage = MemoryGraphStorage()
    mutations = ArenaMutations(storage)
    add_arena(mutations, "first")
    dynamic = CollectionArenaReadGrant(scope="devgraph.arena.read", all_arenas=True)
    fixed = ArenaReadGrant(scope="devgraph.arena.read", arena_ids=["first"])
    assert [r["id"] for r in project_monitor_read(storage, [dynamic], "/arenas")["items"]] == [
        "first"
    ]
    add_arena(mutations, "later")
    assert [r["id"] for r in project_monitor_read(storage, [dynamic], "/arenas")["items"]] == [
        "first",
        "later",
    ]
    assert project_monitor_read(storage, [dynamic], "/arenas/later")["id"] == "later"
    assert [r["id"] for r in project_monitor_read(storage, [fixed], "/arenas")["items"]] == [
        "first"
    ]
    with pytest.raises(KeyError):
        project_monitor_read(storage, [fixed], "/arenas/later")
    with pytest.raises(ForbiddenError):
        project_monitor_read(storage, [dynamic], "/arenas/later/members")
    with pytest.raises(ForbiddenError):
        project_monitor_read(storage, [dynamic], "/todos/v2")


def test_fixed_selector_cannot_become_dynamic_and_legacy_draft_stays_closed(tmp_path):
    fixed = arena_draft(tmp_path)
    raw = json.loads(canonical_json(fixed))
    raw["schema"] = "devgraph.access-draft.v8"
    raw["read_grants"][-1] = dict(scope="devgraph.arena.read", all_arenas=True)
    dynamic = CollectionArenaAccessDraft.model_validate(raw)
    with pytest.raises(ValueError, match="read grant amplification"):
        require_subset(dynamic, fixed)
    require_subset(fixed, dynamic)
    require_subset(dynamic, dynamic)
    legacy = dict(raw, schema="devgraph.access-draft.v6")
    with pytest.raises(ValueError):
        ArenaAccessDraft.model_validate(legacy)
    with pytest.raises(ValueError):
        CollectionArenaReadGrant(scope="devgraph.arena.read", all_arenas=True, arena_ids=["first"])


def test_dynamic_holder_scope_can_narrow_to_fixed_ids_without_expanding_conditions():
    from devgraph.auth.holder_grants import constraint

    p = dict(
        operation="devgraph.arena.get",
        resource="work",
        scope=dict(mode="all"),
        constraints=dict(scope="devgraph.arena.read", all_arenas=True),
    )
    assert constraint(p)["constraints"]["all_arenas"] is True
    p["scope"] = dict(mode="paths", roots=[[dict(kind="Arena", id="later")]])
    assert constraint(p)["constraints"] == dict(scope="devgraph.arena.read", arena_ids=["later"])


def test_ordinary_review_approval_persists_dynamic_rule_and_preserves_fixed_assignment(tmp_path):
    import secrets

    from tests.native_read.test_access_operator import signed
    from tests.native_read.test_path_access_flow import request, setup
    from tests.native_read.test_standing_access import standing

    from devgraph.auth.arena_access import (
        OwnerArenaAccessDraft,
        OwnerArenaMemberPolicy,
        OwnerArenaOperatorPolicy,
    )
    from devgraph.auth.arena_activation import (
        ArenaAccessOperator,
        ArenaAccessRequestInbox,
        OwnerArenaGrantProvisioning,
    )

    s = setup(tmp_path)
    raw = json.loads(canonical_json(standing(s.base[0].ceiling)))
    raw.update(schema="devgraph.access-draft.v7")
    raw["read_grants"].append(dict(scope="devgraph.arena.read", all_arenas=True))
    root = OwnerArenaAccessDraft.model_validate(raw)
    fixed_raw = dict(
        raw,
        schema="devgraph.access-draft.v6",
        subject_public_key="f" * 64,
        installation_id="b" * 32,
        read_grants=[dict(scope="devgraph.arena.read", arena_ids=["a"])],
        write_grants=[],
    )
    fixed = ArenaAccessDraft.model_validate(fixed_raw)
    original = canonical_json(fixed)
    s.base = [
        OwnerArenaMemberPolicy.model_validate(
            dict(
                schema="devgraph.member-access-policy.v6",
                revision=3,
                ceiling=root,
                assignments=[fixed],
            )
        )
    ]
    policy = s.policies[0].model_dump(mode="json", by_alias=True)
    policy.update(
        schema="devgraph.access-operator-policy.v6",
        revision=4,
        ceiling=json.loads(canonical_json(root)),
    )
    s.policies = [OwnerArenaOperatorPolicy.model_validate(policy)]
    s.inbox = ArenaAccessRequestInbox(
        path=s.inbox.path,
        issuer=root.issuer,
        receiver=root.receiver,
        membership_check=s.inbox.membership_check,
        catalog_policy=lambda: s.policies[0],
        storage=s.storage,
    )
    provisioning = OwnerArenaGrantProvisioning(
        inbox=s.inbox,
        members=lambda: s.base[0],
        operators=lambda: s.policies[0],
        native=lambda: s.native[0],
        native_admission=object(),
    )
    service = ArenaAccessOperator(
        inbox=s.inbox, policy=lambda: s.policies[0], storage=s.storage, provisioning=provisioning
    )
    request(s)

    def invoke(action, **fields):
        value = dict(
            schema="devgraph.access-operator-action.v12",
            action=action,
            action_id=secrets.token_hex(16),
            **fields,
        )
        challenge = service.challenge(s.key, "a" * 32, service.parse_action(value))
        return service.execute(challenge, signed(s.operator, challenge))

    inbox = invoke("inbox")
    assert inbox["activation"]["future_arena_reads"] is True
    item = inbox["inbox"]["items"][0]
    selected = dict(
        raw,
        schema="devgraph.access-draft.v8",
        subject_public_key=s.public,
        installation_id="a" * 32,
        previous_digest=root.digest,
        read_grants=[dict(scope="devgraph.arena.read", all_arenas=True)],
        write_grants=[],
    )
    fields = dict(
        request_id=item["request_id"],
        expected_version=item["version"],
        request_digest_sha256=item["request_digest_sha256"],
        previous_assignment_digest=item["previous_assignment_digest"],
        selection=dict(read=selected),
    )
    review = invoke("review", **fields)
    assert review["selection"]["read"]["read_grants"] == [
        dict(scope="devgraph.arena.read", all_arenas=True)
    ]
    assert review["plans"]["activation"]["validity"]["mode"] == "until_revoked"
    accepted = invoke("approve", review_id=review["review_id"], **fields)
    assert accepted["activation"]["validity"]["mode"] == "until_revoked"
    inbox_after = invoke("inbox")
    assert inbox_after["active_grants"][0]["validity"]["mode"] == "until_revoked"
    current = provisioning.registry()
    assert (
        canonical_json(next(a for a in current.assignments if a.subject_public_key == "f" * 64))
        == original
    )
    active = next(a for a in current.assignments if a.subject_public_key == s.public)
    assert active.read_grants[0].all_arenas is True
    assert canonical_json(current.ceiling) == canonical_json(root)
    with pytest.raises(ValueError):
        service.parse_action(
            dict(
                schema="devgraph.access-operator-action.v8",
                action="review",
                action_id=secrets.token_hex(16),
                **fields,
            )
        )


def test_dynamic_collection_pages_beyond_fixed_id_limit_and_excludes_archived():
    from tests.model.test_arena_runtime import arena_command

    storage = MemoryGraphStorage()
    service = ArenaMutations(storage)
    assert storage.delegated_arena_page((), all_resources=True) == []
    for number in range(270):
        add_arena(service, f"arena-{number:03}")
    service.execute(arena_command("archive", subject="arena-269", version=1))
    found = []
    after = None
    while page := storage.delegated_arena_page((), all_resources=True, after_id=after, limit=50):
        found.extend(row.id for row in page)
        after = page[-1].id
    assert found == [f"arena-{number:03}" for number in range(269)]
    assert len(found) == len(set(found))
    assert [row.id for row in storage.delegated_arena_page(("arena-000",))] == ["arena-000"]
    for ids, flag in [((), False), (("arena-000",), True), ((), 1), ((), "true")]:
        with pytest.raises(ValueError):
            storage.delegated_arena_page(ids, all_resources=flag)


def test_neo4j_collection_selector_is_parameterized_and_bounded_before_return():
    from tests.storage.test_neo4j_persistence_contract import _storage_with_rows

    storage, calls = _storage_with_rows([])
    assert storage.delegated_arena_page((), all_resources=True, after_id="after", limit=20) == []
    query, parameters = calls[0]
    assert "($all_resources OR n.id IN $arena_ids)" in query
    assert "n.archived = false" in query
    assert "ORDER BY n.id LIMIT $limit" in query
    assert parameters == dict(arena_ids=[], all_resources=True, after_id="after", limit=20)
    for ids, flag in [((), False), (("a",), True), ((), 1)]:
        with pytest.raises(ValueError):
            storage.delegated_arena_page(ids, all_resources=flag)
    assert len(calls) == 1
