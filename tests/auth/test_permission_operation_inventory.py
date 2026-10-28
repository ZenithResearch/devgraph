"""The reviewed inventory classifies operations; it never creates authority."""
import json
from pathlib import Path

from devgraph.arena_requests import ARENA_OPERATIONS
from devgraph.auth.access_policy import WRITE_OPERATIONS, WriteGrant
from devgraph.work_requests import WORK_OPERATIONS_V2

ROOT = Path(__file__).parents[2]


def test_every_named_mutation_has_one_reviewed_editor_mapping():
    inventory = json.loads((ROOT / "docs/auth/permission-operation-inventory.json").read_text())
    rows = inventory["named_mutations"]
    ids = {row["operation"] for row in rows}
    canonical = {f"devgraph.work.{op}.v2" for op in WORK_OPERATIONS_V2}
    canonical |= {f"devgraph.arena.{op}.v1" for op in ARENA_OPERATIONS}
    assert ids == canonical == WRITE_OPERATIONS
    assert len(ids) == len(rows) == 22
    assert inventory["authority"] is False
    for row in rows:
        assert row["classification"] == "recipient_rule"
        assert set(row["selectors"]) <= {"all_permitted", "exact_ids", "verified_hierarchy"}
        assert row["resource_requirements"]
        WriteGrant.model_validate(dict(operation=row["operation"], resources=[dict(kind="Task")]))


def test_every_ontology_operation_is_classified_without_claiming_receiver_support():
    inventory = json.loads((ROOT / "docs/auth/permission-operation-inventory.json").read_text())
    releases = ROOT / "src/devgraph/resources/ontology-releases"
    latest = max((p for p in releases.iterdir() if (p / "operations.json").is_file()),
                 key=lambda p: tuple(map(int, p.name.removeprefix("v").split("."))))
    ontology = json.loads((latest / "operations.json").read_text())
    rows = inventory["named_mutations"] + inventory["other_ontology_operations"]
    assert len(rows) == len({row["operation"] for row in rows})
    assert {row["operation"] for row in rows} == {row["name"] for row in ontology["operations"]}
