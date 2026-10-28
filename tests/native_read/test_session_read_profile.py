"""Lossless provider mapping; ordinary stored/signed grants are never rewritten."""

import copy
import json
from types import SimpleNamespace

import pytest
from tests.native_read.test_arena_access import arena_draft

from devgraph.auth.arena_access import (
    CollectionArenaAccessDraft,
    CollectionArenaGrantSelection,
    OwnerArenaAccessDraft,
    ReadAssignment,
    compile_arena_read,
)
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.session_read_profile import (
    build_read_session_selection,
    read_session_permissions,
)

PIN = dict(service="service:fixture", issuer="fixture", key_id="fixture-key", public_key="ab" * 32)
IDENTITY = dict(public_key="cd" * 32, installation_id="ef" * 16)


def profile(tmp_path, dynamic):
    ceiling = arena_draft(tmp_path)
    if dynamic:
        raw = json.loads(canonical_json(ceiling))
        raw["schema"] = "devgraph.access-draft.v7"
        raw["read_grants"][-1] = dict(scope="devgraph.arena.read", all_arenas=True)
        ceiling = OwnerArenaAccessDraft.model_validate(raw)
    provisioning = SimpleNamespace(
        decode_draft=ReadAssignment.validate_json, selection_type=CollectionArenaGrantSelection
    )
    return ceiling, provisioning


def selection(ceiling, provisioning, permissions):
    return build_read_session_selection(
        ceiling=ceiling,
        provisioning=provisioning,
        compile_read=compile_arena_read,
        issuer_pin=PIN,
        identity=IDENTITY,
        permissions=permissions,
        not_before=ceiling.not_before + 1,
        expires_at=ceiling.not_before + 301,
    )


@pytest.mark.parametrize("dynamic", [False, True])
def test_arena_scope_and_full_constraints_round_trip_without_rewriting_ceiling(tmp_path, dynamic):
    ceiling, provisioning = profile(tmp_path, dynamic)
    original = canonical_json(ceiling)
    # Owner v7 is not a member grant; the ordinary v8 draft carries explicit All.
    raw = json.loads(original)
    if dynamic:
        raw["schema"] = "devgraph.access-draft.v8"
    member = ReadAssignment.validate_json(canonical_json(raw))
    permissions = read_session_permissions(member, compile_arena_read, PIN)
    selected = selection(ceiling, provisioning, permissions)
    assert selected.write is None and not selected.read.write_grants
    assert selected.read.subject_public_key == IDENTITY["public_key"]
    assert selected.read.validity.not_after == ceiling.not_before + 301
    assert read_session_permissions(selected.read, compile_arena_read, PIN) == permissions
    assert canonical_json(ceiling) == original
    if dynamic:
        assert isinstance(selected.read, CollectionArenaAccessDraft)
        assert selected.read.read_grants[-1].all_arenas is True
    else:
        assert selected.read.read_grants[-1].arena_ids == ("arena-one",)


@pytest.mark.parametrize(
    "mutation", ["other-service", "broader-selector", "write-operation", "extra-role"]
)
def test_mapping_cannot_discard_roles_or_change_operation_resource_meaning(tmp_path, mutation):
    ceiling, provisioning = profile(tmp_path, False)
    permissions = read_session_permissions(ceiling, compile_arena_read, PIN)
    changed = copy.deepcopy(permissions)
    if mutation == "other-service":
        changed[-1]["roles"][0]["selector"]["service"] = "service:other"
    elif mutation == "broader-selector":
        changed[-1]["roles"][0]["selector"]["selection"] = dict(mode="all")
    elif mutation == "write-operation":
        changed[-1]["operation"] = "devgraph.arena.create"
    else:
        changed[-1]["roles"].append(copy.deepcopy(changed[-1]["roles"][0]))
    with pytest.raises(ValueError):
        selection(ceiling, provisioning, changed)


def test_single_arena_operation_cannot_silently_acquire_its_paired_read_operation(tmp_path):
    ceiling, provisioning = profile(tmp_path, False)
    permissions = [
        p
        for p in read_session_permissions(ceiling, compile_arena_read, PIN)
        if p["operation"] == "devgraph.arena.get"
    ]
    with pytest.raises(ValueError):
        selection(ceiling, provisioning, permissions)


def test_hierarchy_ancestors_and_object_identity_survive_mapping(tmp_path):
    from tests.auth.test_resource_paths import read

    from devgraph.auth.arena_access import ArenaAccessDraft

    ceiling, provisioning = profile(tmp_path, False)
    raw = json.loads(canonical_json(ceiling))
    raw["read_grants"] = [json.loads(canonical_json(read("Arena/a", "Project/project-a")))]
    ceiling = ArenaAccessDraft.model_validate(raw)
    permissions = read_session_permissions(ceiling, compile_arena_read, PIN)
    mapped = selection(ceiling, provisioning, permissions)
    assert mapped.read.read_grants[0].resource_paths == ceiling.read_grants[0].resource_paths
    assert read_session_permissions(mapped.read, compile_arena_read, PIN) == permissions
    stripped = copy.deepcopy(permissions)
    for permission in stripped:
        permission["constraints"]["resource_paths"][0]["path"] = ["Project/project-a"]
    with pytest.raises(ValueError):
        selection(ceiling, provisioning, stripped)


def test_dynamic_collection_cannot_be_mapped_under_fixed_id_ceiling(tmp_path):
    ceiling, provisioning = profile(tmp_path, False)
    raw = json.loads(canonical_json(ceiling))
    raw.update(schema="devgraph.access-draft.v8")
    raw["read_grants"][-1] = dict(scope="devgraph.arena.read", all_arenas=True)
    requested = CollectionArenaAccessDraft.model_validate(raw)
    with pytest.raises(ValueError):
        selection(
            ceiling, provisioning, read_session_permissions(requested, compile_arena_read, PIN)
        )
