"""Validate Arena runtime publication without broadening Work kinds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from devgraph.api.schemas import WorkKind
from devgraph.model.validation import CANONICAL_WORK_KINDS

ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY = ROOT / "ontology"
PROFILE = json.loads((ONTOLOGY / "arena-contract.json").read_text())


def test_arena_vocabulary_and_profile_agree_without_broadening_work_kinds() -> None:
    ontology = json.loads((ONTOLOGY / "ontology.jsonld").read_text())
    publication = json.loads((ONTOLOGY / "publication.json").read_text())
    by_id = {item["@id"]: item for item in ontology["@graph"]}
    arena = by_id["zn:Arena"]
    assert arena["@type"] == "owl:Class"
    assert arena["runtimeLabel"] is True
    assert "subClassOf" not in arena
    edge = by_id["zn:CONTAINS_WORK"]
    assert (edge["domain"], edge["range"]) == ("zn:Arena", "zn:Todo")
    assert PROFILE["ontology_version"] == publication["version"] == "0.6.0"
    assert PROFILE["runtime_binding"] is True
    assert PROFILE["arena"]["subclass_of_todo"] is False
    assert PROFILE["arena"]["nested_arenas"] is False
    assert set(get_args(WorkKind)) == {"Proposal", "Initiative", "Project", "Issue", "Task"}
    assert "Arena" not in CANONICAL_WORK_KINDS


def test_parentless_and_inherited_membership_rules_are_explicit() -> None:
    direct = PROFILE["direct_membership"]
    assert direct["predicate"] == "CONTAINS_WORK"
    assert direct["direction"] == "Arena_to_Work"
    assert direct["allowed_kinds"] == ["Initiative", "Task"]
    assert direct["requires_no_incoming_work_parent"] is True
    assert direct["work_parent_predicate"] == "HAS_CHILD"
    assert direct["archived_parent_counts_as_parent"] is True
    assert direct["minimum_arenas_per_member"] == 0
    assert direct["maximum_arenas_per_member"] == 1
    assert direct["duplicate_membership_edges_allowed"] is False
    assert direct["bare_todo_is_concrete_kind"] is False
    derived = PROFILE["derived_membership"]
    assert derived["follow"] == "incoming_HAS_CHILD_to_work_root"
    assert derived["inherit_root_arena"] is True
    assert derived["persist_inherited_edges"] is False
    assert derived["reject_multiple_parents_or_cycles"] is True
    assert derived["dependencies_and_blockers_confer_membership"] is False
    assert not any(PROFILE["authority"].values())


@pytest.mark.parametrize("example", PROFILE["examples"], ids=lambda item: item["name"])
def test_published_membership_examples_conform_to_the_profile(example: dict) -> None:
    # Interpret direct-edge admission only, not a production graph mutation.
    direct = PROFILE["direct_membership"]
    allowed = (
        example["kind"] in direct["allowed_kinds"]
        and not example["incoming_work_parents"]
        and len(example["existing_direct_arenas"]) < direct["maximum_arenas_per_member"]
    )
    assert allowed is example["allowed"]


def test_arena_contract_is_published_in_both_canonical_and_cli_bundles() -> None:
    publication = json.loads((ONTOLOGY / "publication.json").read_text())
    for root in (ONTOLOGY / "releases", ROOT / "src/devgraph/resources/ontology-releases"):
        release = root / publication["release"]
        manifest = json.loads((release / "manifest.json").read_text())
        paths = {item["path"] for item in manifest["files"]}
        assert {"arenas.md", "arena-contract.json"} <= paths
        assert json.loads((release / "arena-contract.json").read_text()) == PROFILE
        assert (release / "arenas.md").read_bytes() == (ONTOLOGY / "arenas.md").read_bytes()


def test_ontology_extension_preserves_existing_operations_and_constraints() -> None:
    old_root = ONTOLOGY / "releases/v0.4.0"
    previous = json.loads((old_root / "operations.json").read_text())
    current = json.loads((ONTOLOGY / "operations.json").read_text())
    assert current.pop("ontology_version") == "0.6.0"
    assert previous.pop("ontology_version") == "0.4.0"
    old_operations = previous.pop("operations")
    operations = current.pop("operations")
    assert current == previous
    assert operations[:len(old_operations)] == old_operations
    assert {item["name"] for item in operations[len(old_operations):]} == {
        f"devgraph.arena.{op}.v1" for op in ("create", "patch", "archive", "member.set")
    }
    assert (ONTOLOGY / "neo4j/constraints.cypher").read_bytes().startswith(
        (old_root / "constraints.cypher").read_bytes()
    )


def test_arena_runtime_keeps_canonical_containment_rules() -> None:
    classes = (ONTOLOGY / "classes.md").read_text()
    deferred = classes.split("## Watch/defer:", 1)[1].split("## EventReceipt", 1)[0]
    canonical = classes.split("## Canonical cross-Zenith classes", 1)[1]
    assert "`Arena`" not in deferred
    assert "`Arena`" in canonical
    assert ":Arena)" in (ONTOLOGY / "neo4j/constraints.cypher").read_text()
    documentation = (ONTOLOGY / "arenas.md").read_text()
    for phrase in (
        "archived parent still counts", "atomically", "at most one Arena",
        "bare `Todo`", "grants no authority", "runtimeLabel: true",
    ):
        assert phrase in documentation
