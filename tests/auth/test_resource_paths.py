"""Live path access, using synthetic graphs and actual signed Work fixtures."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from tests.auth.test_write_scope import (
    command,
    create,
    execute,
    fixture,
    grant,
    hierarchy,
    policy,
)

from devgraph.auth.access_policy import compile_write, require_subset
from devgraph.auth.delegated_contract import _grant_is_subset, canonical_json
from devgraph.auth.monitor_read import EnrollmentPolicyV2, MonitorWorkGrant, project_monitor_read
from devgraph.auth.path_scope import PathReadGrant, PathWriteGrant, ReceiverPathWritePolicy
from devgraph.auth.secs_work import SecSWorkDenied
from devgraph.auth.write_scope import ReceiverWritePolicy, require_write_scope_subset
from devgraph.resource_paths import ResourcePath, current_resource_path, paths_subset
from devgraph.storage.memory import MemoryGraphStorage


def path(*anchors):
    return ResourcePath.model_validate({"schema": "devgraph.resource-path.v1", "path": anchors})


def read(*anchors, **changes):
    return PathReadGrant.model_validate(
        dict(
            schema="devgraph.path-read-grant.v1",
            scope="devgraph.work.read",
            operations=["list", "get", "relationships"],
            relationship_types=["children", "parent", "dependencies"],
            work_kinds=["Project", "Task"],
            include_archived=False,
            resource_paths=[path(*anchors)],
            **changes,
        )
    )


def write(*anchors, operation="patch", kind="Task", ids=None):
    return PathWriteGrant.model_validate(
        dict(
            grant(operation, kind, ids),
            schema="devgraph.path-write-grant.v1",
            resource_paths=[path(*anchors)],
        )
    )


def path_policy(grants):
    value = policy([grant()]).model_dump(mode="json", by_alias=True)
    value["schema"] = "devgraph.receiver-write-policy.v2"
    value["assignments"][0]["grants"] = grants
    return ReceiverPathWritePolicy.model_validate(value)


def move(storage, child="child", source="project-a", target="project-b"):
    storage.delete_edge("Project", source, "HAS_CHILD", "Task", child)
    storage.create_edge("Project", target, "HAS_CHILD", "Task", child)


def ids(storage, selected):
    result = project_monitor_read(storage, [selected], "/todos/v2?limit=100&archived=exclude")
    return {item["key"] for item in result["items"]}, result["total"]


def test_existing_and_future_children_are_decided_at_each_access():
    storage = MemoryGraphStorage()
    hierarchy(storage)
    selected = read("Arena/a", "Initiative/root-a", "Project/project-a")
    before = canonical_json(selected)
    assert ids(storage, selected) == ({"Project/project-a", "Task/child"}, 2)
    create(storage, "Task", "future")
    storage.create_edge("Project", "project-a", "HAS_CHILD", "Task", "future")
    assert ids(storage, selected)[1] == 3
    move(storage)
    assert ids(storage, selected) == ({"Project/project-a", "Task/future"}, 2)
    move(storage, source="project-b", target="project-a")
    assert ids(storage, selected)[1] == 3
    assert canonical_json(selected) == before  # Same grant, no reissuance or inventory refresh.


def test_parent_anchor_cannot_be_dropped_when_container_moves():
    storage = MemoryGraphStorage()
    hierarchy(storage)
    selected = read("Arena/a", "Project/project-a")
    independent = read("Project/project-a")
    storage.delete_edge("Initiative", "root-a", "HAS_CHILD", "Project", "project-a")
    storage.create_edge("Initiative", "root-b", "HAS_CHILD", "Project", "project-a")
    assert ids(storage, selected) == (set(), 0)
    assert ids(storage, independent)[1] == 2


@pytest.mark.parametrize(
    "bad",
    [
        [],
        ["Task/child"],
        ["Arena/a", "Arena/b"],
        ["Project/p", "Initiative/i"],
        ["Arena/../a"],
        ["Arena/*"],
        ["Arena/a/Project/p"],
        ["Project/UPPER"],
    ],
)
def test_path_is_closed_typed_and_ordered(bad):
    with pytest.raises(ValueError):
        path(*bad)


def test_attenuation_retains_all_ancestors_not_todays_membership():
    parent = (path("Arena/a"),)
    assert paths_subset((path("Arena/a", "Project/p"),), parent)
    assert not paths_subset((path("Project/p"),), parent)
    assert not paths_subset((), parent)
    assert not paths_subset((path("Arena/a"), path("Arena/b")), parent)
    assert not _grant_is_subset(read("Project/p"), read("Arena/a"))
    assert _grant_is_subset(read("Arena/a", "Project/p"), read("Arena/a"))
    parent = path_policy([write("Arena/a")]).assignments[0]
    child = path_policy([write("Arena/a", "Project/p")]).assignments[0]
    require_write_scope_subset(child, parent)
    with pytest.raises(ValueError):
        require_write_scope_subset(path_policy([write("Project/p")]).assignments[0], parent)

    # Shared draft attenuation uses the same rule, without today's graph lookup.
    def draft(grants):
        return SimpleNamespace(
            issuer="i",
            receiver="r",
            write_audience="a",
            subject_public_key=None,
            installation_id=None,
            not_before=1,
            not_after=2,
            lifetime_seconds=60,
            read_grants=(),
            write_grants=grants,
        )

    require_subset(draft(child.grants), draft(parent.grants))
    with pytest.raises(ValueError):
        require_subset(
            draft(path_policy([write("Project/p")]).assignments[0].grants), draft(parent.grants)
        )


@pytest.mark.parametrize("corruption", ["duplicate", "two-parents", "cycle", "membership"])
def test_corrupt_chain_is_denied_even_above_selected_anchor(corruption):
    storage = MemoryGraphStorage()
    hierarchy(storage)
    if corruption == "duplicate":
        storage._edges.append(next(e for e in storage.list_edges() if e.to_id == "project-a"))
    elif corruption == "two-parents":
        storage.create_edge("Initiative", "root-b", "HAS_CHILD", "Project", "project-a")
    elif corruption == "cycle":
        storage.create_edge("Task", "child", "HAS_CHILD", "Initiative", "root-a")
    else:
        storage.create_edge("Arena", "b", "CONTAINS_WORK", "Project", "project-a")
    assert current_resource_path(storage, "Task", "child") is None
    assert ids(storage, read("Project/project-a")) == (set(), 0)


def test_filtering_precedes_count_cursor_and_hidden_relationships():
    storage = MemoryGraphStorage()
    hierarchy(storage)
    create(storage, "Task", "a-hidden")
    create(storage, "Task", "z-visible")
    storage.create_edge("Project", "project-a", "HAS_CHILD", "Task", "z-visible")
    storage.create_edge("Task", "child", "DEPENDS_ON", "Task", "a-hidden")
    selected = read("Project/project-a", work_ids=["Task/z-visible"])
    result = project_monitor_read(storage, [selected], "/todos/v2?limit=1&archived=exclude")
    assert result["total"] == 1 and result["items"][0]["id"] == "z-visible"
    graph = project_monitor_read(storage, [read("Project/project-a")], "/monitor/topology/v1")
    assert "a-hidden" not in {n["id"] for n in graph["graph_nodes"]}
    assert not any(e.get("type") == "DEPENDS_ON" for e in graph["graph_edges"])


def test_complete_projection_holds_graph_transaction_until_capture_finishes(monkeypatch):
    storage = MemoryGraphStorage()
    hierarchy(storage)
    inside = False
    original, page = storage.work_mutation_transaction, storage.delegated_work_page

    @contextmanager
    def locked():
        nonlocal inside
        with original():
            inside = True
            try:
                yield
            finally:
                inside = False

    def checked(*args, **kwargs):
        assert inside
        return page(*args, **kwargs)

    monkeypatch.setattr(storage, "work_mutation_transaction", locked)
    monkeypatch.setattr(storage, "delegated_work_page", checked)
    assert ids(storage, read("Project/project-a"))[1] == 2
    assert not inside


def test_existing_credential_cannot_write_after_object_leaves_path():
    adapter, storage, audit, state = fixture([grant()])
    hierarchy(storage)
    state[0] = path_policy([write("Arena/a", "Project/project-a")])
    execute(adapter, command())
    move(storage)
    with pytest.raises(SecSWorkDenied):
        execute(adapter, command(version=2), "different-request")
    with pytest.raises(SecSWorkDenied):
        execute(adapter, command())  # Even an original receipt lookup requires current scope.
    assert len(storage.query("EventReceipt")) == 1 and len(audit.records) == 1


def test_reparent_cannot_launder_separate_path_grants_and_rolls_back():
    adapter, storage, audit, state = fixture([grant()])
    hierarchy(storage)
    from tests.model.test_named_work import ref

    state[0] = path_policy(
        [
            write("Project/project-a", operation="parent.set"),
            write("Project/project-b", operation="parent.set"),
            grant("parent.set", "Project", ["project-a", "project-b"]),
        ]
    )
    before = storage.query(), storage.list_edges()
    request = command(
        "parent.set",
        payload=dict(
            previous_parent=ref("Project", "project-a", 1), parent=ref("Project", "project-b", 1)
        ),
    )
    with pytest.raises(SecSWorkDenied, match="scope_changed"):
        execute(adapter, request)
    assert before == (storage.query(), storage.list_edges())
    assert audit.records == []


def test_legacy_parsers_and_native_export_never_drop_path_restriction():
    selected = read("Project/project-a")
    with pytest.raises(ValueError):
        MonitorWorkGrant.model_validate_json(canonical_json(selected))
    with pytest.raises(ValueError):
        EnrollmentPolicyV2.model_validate(
            dict(
                schema="devgraph.native-read-policy.v2",
                issuer="fixture",
                receiver="https://example.test",
                lifetime_seconds=600,
                grants=[selected],
            )
        )
    value = path_policy([write("Project/project-a")]).model_dump(mode="json", by_alias=True)
    value["schema"] = "devgraph.receiver-write-policy.v1"
    with pytest.raises(ValueError):
        ReceiverWritePolicy.model_validate(value)
    with pytest.raises(ValueError, match="cannot flatten"):
        compile_write(
            SimpleNamespace(write_grants=[write("Project/project-a")]),
            policy_id="fixture",
            policy_version=1,
        )


def test_neo4j_filters_paths_before_limit_and_unsupported_views_fail_closed():
    from tests.storage.test_neo4j_persistence_contract import _storage_with_rows

    from devgraph.storage.delegated import DelegatedWorkSelector

    storage, calls = _storage_with_rows([])
    selected = DelegatedWorkSelector(resource_paths=(path("Arena/a", "Project/p"),))
    storage.delegated_work_page(
        "Task", selectors=(selected,), work_kinds=("Task",), include_archived=False, limit=1
    )
    query, params = calls[0]
    assert query.index("selector.resource_paths") < query.index("ORDER BY n.id")
    assert "COUNT { MATCH ()-[:HAS_CHILD]->(item) } = 1" in query
    assert params["selectors"][0]["resource_paths"][0]["path"] == ["Arena/a", "Project/p"]
    storage.delegated_arena_member_page(
        "a", selectors=(selected,), work_kinds=("Task",), include_archived=False, limit=1
    )
    member_query, member_params = calls[1]
    assert member_query.index("selector.resource_paths") < member_query.index("LIMIT $limit")
    assert "COUNT { MATCH ()-[:HAS_CHILD]->(item) } = 1" in member_query
    assert member_params["selectors"] == params["selectors"]
    assert member_params["arena_id"] == "a"


def test_published_path_schemas_examples_and_old_object_parsers_match():
    import json
    from pathlib import Path

    from tests.auth.test_access_policy import draft

    from devgraph.auth.access_policy import AccessDraft
    from devgraph.auth.native_read import EnrollmentPolicy

    root = Path(__file__).parents[2] / "docs/contracts/resource-path/v1"
    for name, cls in [
        ("path", ResourcePath),
        ("read-grant", PathReadGrant),
        ("write-grant", PathWriteGrant),
        ("receiver-policy", ReceiverPathWritePolicy),
    ]:
        assert json.loads((root / (name + ".schema.json")).read_text()) == cls.model_json_schema()
        cls.model_validate_json((root / (name + ".example.json")).read_text())
    selected = read("Project/p").model_copy(update={"operations": ("list", "get")})
    with pytest.raises(ValueError):
        EnrollmentPolicy.model_validate(
            dict(
                schema="devgraph.native-read-policy.v1",
                issuer="i",
                receiver="https://example.test",
                lifetime_seconds=600,
                grants=[selected],
            )
        )
    raw = draft().model_dump(mode="json", by_alias=True)
    raw["read_grants"] = [selected]
    with pytest.raises(ValueError):
        AccessDraft.model_validate(raw)


def test_path_constraints_are_preserved_by_real_secs_signature(tmp_path):
    import hashlib
    import json

    from tests.native_read.test_signed_authority import NOW, request, setup

    from devgraph.auth.errors import UnauthenticatedError
    from devgraph.auth.signed_read import SecsReadAuthority

    service, owner, _, _, _ = setup(tmp_path)
    pinned = service.authority.policy()
    policy = SecsReadAuthority.policy_for(
        SimpleNamespace(
            issuer=pinned["issuer"],
            receiver=pinned["receiver"],
            lifetime_seconds=600,
            grants=[read("Arena/a", "Project/p")],
        ),
        key_id=pinned["key_id"],
        public_key=pinned["public_key"],
    )
    current = int(NOW.timestamp())
    claims = dict(
        schema="castalia.read-credential.v1",
        credential_id="d" * 32,
        holder_public_key=owner.public_key().public_bytes_raw().hex(),
        installation_id="a" * 32,
        issuer=policy["issuer"],
        key_id=policy["key_id"],
        receiver=policy["receiver"],
        audience="devgraph",
        policy_digest=hashlib.sha256(canonical_json(policy)).hexdigest(),
        permissions=policy["permissions"],
        issued_at=current,
        expires_at=current + 600,
    )
    credential = service.authority.invoke_under_policy(policy, "issue", claims=claims, now=current)
    verified = service.authority.invoke_under_policy(
        policy, "verify", **request(owner, credential), active=True, now=current
    )
    decoded = PathReadGrant.model_validate(verified["permissions"][0]["constraints"])
    assert decoded.resource_paths == (path("Arena/a", "Project/p"),)
    altered = json.loads(canonical_json(credential))
    altered["claims"]["permissions"][0]["constraints"].pop("resource_paths")
    with pytest.raises(UnauthenticatedError):
        service.authority.invoke_under_policy(
            policy, "verify", **request(owner, altered), active=True, now=current
        )


def test_read_arena_a_and_write_project_b_are_independent_receiver_rules():
    adapter, storage, audit, state = fixture([grant()])
    hierarchy(storage)
    create(storage, "Task", "writable")
    storage.create_edge("Project", "project-b", "HAS_CHILD", "Task", "writable")
    selected_read = read("Arena/a")
    state[0] = path_policy([write("Arena/b", "Project/project-b")])
    visible, _ = ids(storage, selected_read)
    assert "Task/child" in visible
    assert "Task/writable" not in visible
    execute(adapter, command(identifier="writable"))
    with pytest.raises(SecSWorkDenied, match="write_resource_not_granted"):
        execute(adapter, command(identifier="child"), "denied-read-only")
    assert storage.get_node("Task", "child").properties["version"] == 1
    assert storage.get_node("Task", "writable").properties["version"] == 2
    assert len(audit.records) == 1
