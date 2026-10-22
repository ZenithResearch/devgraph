import json
from pathlib import Path

import pytest

from devgraph.ops.owner_setup import compile_operator_policy, decode_plan, plan_digest


def test_shared_native_preset_owner_vectors_compile_exactly():
    path = Path(__file__).parents[1] / "fixtures/owner-setup-v2.json"
    for case in json.loads(path.read_text())["cases"]:
        if not case["accepted"]:
            with pytest.raises((ValueError, TypeError)):
                decode_plan(case["raw"].encode())
            continue
        plan = decode_plan(case["raw"].encode())
        assert plan_digest(plan) == case["digest"], case["name"]
        policy = compile_operator_policy(plan, now=1000)
        if plan.read_operations:
            assert policy.ceiling.read_grants[0].operations == plan.read_operations
        else:
            assert not policy.ceiling.read_grants
        assert tuple(g.operation for g in policy.ceiling.write_grants) == tuple(
            "devgraph.work." + op + ".v2" for op in plan.write_operations
        )
        assert policy.operator_public_keys == (plan.owner_public_key,)
        # Seat references do not become a principal, member assignment or authority.
        raw = json.dumps(policy.model_dump(mode="json", by_alias=True))
        assert "seat_type_iri" not in raw
        assert "assignments" not in raw


def test_v1_reader_keeps_original_permissions_and_digest():
    path = Path(__file__).parents[1] / "fixtures/owner-setup-v1.json"
    for case in json.loads(path.read_text())["cases"]:
        if case["accepted"]:
            plan = decode_plan(case["raw"].encode())
            assert plan.read_operations is None
            assert plan_digest(plan) == case["digest"]
