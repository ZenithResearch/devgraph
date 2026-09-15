from __future__ import annotations

from types import MethodType

import pytest

from devgraph.model.work import Requirement
from devgraph.storage.base import NodeRecord
from devgraph.storage.memory import MemoryGraphStorage
from devgraph.storage.neo4j import Neo4jGraphStorage
from devgraph.storage.supporting_material import SupportingReference


def neo4j_rows(rows):
    storage = object.__new__(Neo4jGraphStorage)
    calls = []

    def run_graph(self, query, **parameters):
        calls.append((query, parameters))
        return rows

    storage._run_graph = MethodType(run_graph, storage)
    return storage, calls


def test_memory_and_neo4j_reference_contract_and_parent_specific_bounded_query():
    memory = MemoryGraphStorage()
    for kind, item_id in [
        ("Task", "parent"),
        ("Task", "other"),
        ("Artifact", "a"),
        ("Requirement", "r"),
        ("AcceptanceCriterion", "c"),
    ]:
        memory.create_node(kind, item_id, {"title": item_id})
    memory.create_edge("Task", "parent", "HAS_ARTIFACT", "Artifact", "a")
    memory.create_edge("Task", "parent", "HAS_REQUIREMENT", "Requirement", "r")
    memory.create_edge("Requirement", "r", "HAS_ACCEPTANCE_CRITERION", "AcceptanceCriterion", "c")
    expected = [
        SupportingReference("AcceptanceCriterion", "c", ("requirement/r",)),
        SupportingReference("Artifact", "a", ("edge/HAS_ARTIFACT", "field/artifact_ids")),
        SupportingReference("Artifact", "missing", ("field/artifact_ids",)),
        SupportingReference("ExternalLink", "link", ("field/external_link_ids",)),
        SupportingReference("Requirement", "r", ("edge/HAS_REQUIREMENT",)),
    ]
    neo4j, calls = neo4j_rows(
        [
            {"kind": ref.kind, "id": ref.id, "paths": list(ref.paths), "paths_truncated": False}
            for ref in expected
        ]
    )
    kwargs = {"artifact_ids": ("a", "missing"), "external_link_ids": ("link",), "limit": 6}
    assert memory.supporting_material_references("Task", "parent", **kwargs) == expected
    assert neo4j.supporting_material_references("Task", "parent", **kwargs) == expected
    query, parameters = calls[0]
    assert query.count("(source:`Task` {id: $node_id})") == 2
    assert "MATCH (source)-[edge]->(target)" not in query
    assert "LIMIT $limit" in query and "paths[..$max_provenance]" in query
    assert "target_resource" in query and "after_resource" in query
    assert parameters == {
        "node_id": "parent",
        "artifact_ids": ["a", "missing"],
        "external_link_ids": ["link"],
        "after_resource": None,
        "target_resource": None,
        "limit": 6,
        "max_provenance": 100,
    }
    # Both cursor and exact-target filtering are parent-scoped; no field references are ambient.
    assert (
        memory.supporting_material_references(
            "Task", "other", artifact_ids=(), external_link_ids=()
        )
        == []
    )
    assert memory.supporting_material_references(
        "Task", "parent", **kwargs, target_resource="Artifact/missing"
    ) == [expected[2]]
    assert (
        memory.supporting_material_references(
            "Task", "parent", **kwargs, after_resource="Artifact/a"
        )
        == expected[2:]
    )


def test_provenance_is_deduplicated_and_bounded_with_explicit_truncation():
    storage = MemoryGraphStorage()
    storage.create_node("Task", "parent")
    storage.create_node("AcceptanceCriterion", "criterion")
    for index in range(105):
        item_id = f"req-{index:03}"
        storage.create_node("Requirement", item_id)
        storage.create_edge("Task", "parent", "HAS_REQUIREMENT", "Requirement", item_id)
        storage.create_edge(
            "Requirement", item_id, "HAS_ACCEPTANCE_CRITERION", "AcceptanceCriterion", "criterion"
        )
    result = storage.supporting_material_references(
        "Task",
        "parent",
        artifact_ids=(),
        external_link_ids=(),
        target_resource="AcceptanceCriterion/criterion",
        limit=1,
    )
    assert len(result) == 1 and len(result[0].paths) == 100 and result[0].paths_truncated


def test_target_metadata_hydrates_generic_and_canonical_profiles_without_rewriting_legacy():
    requirement = Requirement(id="req", title="Required", description="Text")
    rows = [
        {
            "labels": ["Artifact"],
            "id": "artifact",
            "archived": True,
            "properties": {
                "id": "artifact",
                "archived": True,
                "__devgraph_generic_encoding": "json-v1",
                "title": '__devgraph_generic_json_v1__:"Plan"',
                "role": '__devgraph_generic_json_v1__:"old-role"',
            },
        },
        {
            "labels": ["Requirement"],
            "id": "req",
            "archived": False,
            "properties": {"id": "req", "archived": False, **requirement.to_node_properties()},
        },
        {
            "labels": ["Requirement"],
            "id": "malformed",
            "archived": False,
            "properties": {
                "id": "malformed",
                "archived": False,
                "kind": "Requirement",
                "title": ["invalid"],
            },
        },
    ]
    neo4j, calls = neo4j_rows(rows)
    nodes = neo4j.supporting_material_nodes(
        [("Artifact", "artifact"), ("Requirement", "req"), ("Requirement", "malformed")]
    )
    assert nodes[0] == NodeRecord(
        "Artifact", "artifact", {"title": "Plan", "role": "old-role"}, True
    )
    assert nodes[1] == NodeRecord("Requirement", "req", requirement.to_node_properties())
    assert nodes[2].properties["title"] == ["invalid"]
    assert calls[0][1]["limit"] == 4
    assert "MATCH (n:`Artifact` {id: ref.id})" in calls[0][0]
    assert ".title" in calls[0][0] and "AS properties LIMIT $limit" in calls[0][0]
    assert "properties(n)" not in calls[0][0] and ".raw_payload" not in calls[0][0]
    # Existing canonical decoder still fails closed; only this projection is tolerant.
    with pytest.raises(Exception, match="malformed canonical work object"):
        neo4j._node_from_row(rows[2], "Requirement", expected_id="malformed")


@pytest.mark.parametrize("storage", [MemoryGraphStorage(), neo4j_rows([])[0]])
def test_adapters_reject_unbounded_or_injectable_reads_before_storage(storage):
    for kwargs in (
        {"limit": 102},
        {"limit": True},
        {"target_resource": "Secret/x"},
        {"after_resource": "Artifact/x/extra"},
    ):
        with pytest.raises(ValueError):
            storage.supporting_material_references(
                "Task", "parent", artifact_ids=(), external_link_ids=(), **kwargs
            )
    with pytest.raises(ValueError):
        storage.supporting_material_references(
            "Task`) MATCH (n)", "parent", artifact_ids=(), external_link_ids=()
        )
    with pytest.raises(ValueError):
        storage.supporting_material_nodes([("Secret", "private")])
    with pytest.raises(ValueError):
        storage.supporting_material_nodes([("Artifact", "x")] * 101)


def test_memory_projection_does_not_collect_nonmetadata_payloads():
    memory = MemoryGraphStorage()
    memory.create_node(
        "Artifact",
        "plan",
        {"title": "Plan", "raw_payload": {"secret": "private"}, "password": "private"},
    )
    assert memory.supporting_material_nodes([("Artifact", "plan")]) == [
        NodeRecord("Artifact", "plan", {"title": "Plan"})
    ]


def test_neo4j_absent_projected_fields_match_memory_missing_fields():
    rows = [
        {
            "labels": ["Artifact"],
            "id": "plan",
            "archived": False,
            "properties": {
                "id": "plan",
                "archived": False,
                "title": "Plan",
                "uri": None,
                "__devgraph_generic_encoding": None,
            },
        }
    ]
    neo4j, _ = neo4j_rows(rows)
    assert neo4j.supporting_material_nodes([("Artifact", "plan")]) == [
        NodeRecord("Artifact", "plan", {"title": "Plan"})
    ]
