import copy
import json
from pathlib import Path

import pytest

from devgraph.ops.owner_setup import compile_operator_policy, decode_plan, plan_digest

VECTOR = Path(__file__).parents[1] / "fixtures/owner-setup-v1.json"


def test_shared_transfer_owner_setup_vectors():
    for case in json.loads(VECTOR.read_text())["cases"]:
        if case["accepted"]:
            plan = decode_plan(case["raw"].encode())
            assert plan_digest(plan) == case["digest"]
            policy = compile_operator_policy(plan, now=1000)
            assert policy.operator_public_keys == ("11" * 32,)
            assert policy.ceiling.not_after == plan.not_after
            assert policy.ceiling.subject_public_key is None
            assert len(policy.ceiling.write_grants) == len(plan.write_operations)
        else:
            with pytest.raises((ValueError, TypeError)):
                decode_plan(case["raw"].encode())


def good():
    case = next(
        c for c in json.loads(VECTOR.read_text())["cases"] if c["name"] == "global-standing-manager"
    )
    return json.loads(case["raw"])


def test_global_ceiling_is_not_a_fixture_path_and_does_not_mint_member_access():
    policy = compile_operator_policy(decode_plan(json.dumps(good()).encode()), now=1000)
    raw = policy.model_dump(mode="json", by_alias=True)
    assert policy.ceiling.issuer == "devgraph-5b4e45ac-6e51-43ee-aad0-f1818a43f252"
    assert all(not getattr(g, "resource_paths", ()) for g in policy.ceiling.read_grants)
    assert all(not getattr(g, "resource_paths", ()) for g in policy.ceiling.write_grants)
    assert "assignments" not in raw
    assert not policy.published_paths
    assert "preset-qualification" not in json.dumps(raw)


def test_every_selected_container_remains_a_dynamic_path_predicate():
    case = next(
        c for c in json.loads(VECTOR.read_text())["cases"] if c["name"] == "multiple-standing-paths"
    )
    plan = decode_plan(case["raw"].encode())
    policy = compile_operator_policy(plan, now=1000)
    assert len(policy.ceiling.read_grants[0].resource_paths) == 2
    assert all(
        tuple(p.path) == tuple(n.path)
        for p, n in zip(policy.ceiling.read_grants[0].resource_paths, plan.paths, strict=True)
    )
    assert all(
        g.resource_paths == policy.ceiling.read_grants[0].resource_paths
        for g in policy.ceiling.write_grants
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(status="active"),
        lambda p: p.update(local_endpoint="https://192.168.1.77:18891"),
        lambda p: p.update(owner_public_key="not-an-identity"),
        lambda p: p.update(scope_mode="paths", paths=[]),
        lambda p: p.update(not_after=100),
        lambda p: p.update(write_operations=["access.admin"]),
        lambda p: p.update(wallet_endpoint="https://public.example:18443"),
        lambda p: p.update(wallet_endpoint="https://192.168.1.77:8080"),
        lambda p: p.update(wallet_endpoint="https://127.0.0.1:18443/path"),
        lambda p: p.update(extra="not allowed"),
    ],
)
def test_unreviewed_or_broadened_setup_fields_fail_closed(mutate):
    value = copy.deepcopy(good())
    mutate(value)
    with pytest.raises((ValueError, TypeError)):
        decode_plan(json.dumps(value).encode())


def test_expired_setup_cannot_compile_a_current_policy():
    value = good()
    value.update(not_after=1001)
    plan = decode_plan(json.dumps(value).encode())
    with pytest.raises(ValueError):
        compile_operator_policy(plan, now=1001)


def test_duplicate_decoded_fields_and_oversized_input_fail():
    raw = json.dumps(good())
    with pytest.raises(ValueError):
        decode_plan(raw[:-1].encode() + b',"owner_public_key":"' + b"22" * 32 + b'"}')
    with pytest.raises(ValueError):
        decode_plan(b" " * 65537)


def test_global_publication_cannot_silently_publish_an_unreviewed_hierarchy():
    from devgraph.resource_paths import ResourcePath

    value = good()
    value["publish_hierarchy"] = True
    plan = decode_plan(json.dumps(value).encode())
    with pytest.raises(ValueError, match="scope inventory"):
        compile_operator_policy(plan, now=1000)
    root = ResourcePath.model_validate(
        {"schema": "devgraph.resource-path.v1", "path": ["Arena/real-area"]}
    )
    policy = compile_operator_policy(plan, now=1000, published_global_roots=(root,))
    assert [p.resource_path for p in policy.published_paths] == [root]
