"""Real native admission plus shared secS checks in the mutation transaction."""

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.auth.test_write_scope import command, create, execute, fixture, grant, hierarchy, ref

from devgraph.auth.holder_grants import invoke_native
from devgraph.auth.secs_work import SecSWorkDenied


def shared_host():
    raw = os.environ.get("SECS_HOLDER_GRANT_TEST_BINARY")
    if not raw:
        pytest.skip("native shared authority required")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    binary = Path(raw)
    h = SimpleNamespace(
        service="service:graph",
        binary=binary,
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        pin={
            "issuer": "provider",
            "key_id": "fixture",
            "service": "service:graph",
            "public_key": key.public_key().public_bytes_raw().hex(),
        },
    )
    h.invoke = lambda operation, **fields: invoke_native(
        h.binary, h.binary_sha256, h.pin, operation, **fields
    )
    return h


def test_mixed_rules_cannot_launder_move_but_one_complete_rule_can():
    child = grant("parent.set", "Task", ["child"])
    parent = grant("parent.set", "Project", ["project-a", "project-b"])
    a, s, log, _ = fixture([child, parent])
    a.compound_host = shared_host()
    hierarchy(s)
    r = command(
        "parent.set",
        payload=dict(
            previous_parent=ref("Project", "project-a", 1), parent=ref("Project", "project-b", 1)
        ),
    )
    before = s.query(), s.list_edges()
    with pytest.raises(SecSWorkDenied, match="compound_rule_not_granted"):
        execute(a, r)
    assert (s.query(), s.list_edges()) == before and not log.records
    whole = dict(child, resources=child["resources"] + parent["resources"])
    a, s, log, _ = fixture([whole])
    a.compound_host = shared_host()
    hierarchy(s)
    work, _, duplicate = execute(a, r)
    assert work.version == 2 and not duplicate


def test_prospective_create_and_postcheck_rollback():
    a, s, log, _ = fixture([grant("create", "Task", ["new"])])
    a.compound_host = shared_host()
    r = command("create", "Task", "new", None, dict(id="new", title="Created"))
    work, _, _ = execute(a, r)
    assert work.id == "new"
    a, s, log, _ = fixture([grant("patch", "Task", ["one"])])
    a.compound_host = shared_host()
    create(s, "Task", "one")
    original = a.mutations.execute

    def corrupt(request, **kwargs):
        item = original(request, **kwargs)
        s._nodes.pop(("Task", "one"))
        return item

    a.mutations.execute = corrupt
    with pytest.raises(SecSWorkDenied):
        execute(a, command("patch", "Task", "one"))
    assert s.get_node("Task", "one").properties["title"] == "Fixture" and not log.records


def test_expiry_during_mutation_rolls_back_before_commit():
    from dataclasses import replace

    from tests.auth.test_secs_work import NOW

    from devgraph.auth.secs_work_v2 import SecSWorkV2Verifier

    a, s, log, _ = fixture([grant("patch", "Task", ["one"])])
    a.compound_host = shared_host()
    clock = [NOW]
    a.verifier = SecSWorkV2Verifier(replace(a.verifier.config, clock=lambda: clock[0]))
    create(s, "Task", "one")
    original = a.mutations.execute

    def expire(request, **kwargs):
        result = original(request, **kwargs)
        clock[0] += 61
        return result

    a.mutations.execute = expire
    with pytest.raises(SecSWorkDenied, match="not_current"):
        execute(a, command("patch", "Task", "one"))
    assert s.get_node("Task", "one").properties["title"] == "Fixture"
    assert not log.records
