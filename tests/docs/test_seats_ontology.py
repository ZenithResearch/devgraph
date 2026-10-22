import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
ONTOLOGY = ROOT / "ontology"


def test_shared_seats_support_people_and_agents_without_runtime_authority():
    profile = json.loads((ONTOLOGY / "seats.json").read_text())
    graph = json.loads((ONTOLOGY / "ontology.jsonld").read_text())["@graph"]
    terms = {item["@id"]: item for item in graph}
    for name in [
        "Seat",
        "SeatType",
        "SeatAssignment",
        "CredentialPreset",
        "CredentialPresetRevision",
    ]:
        assert terms["zn:" + name]["runtimeLabel"] is False
        assert "subClassOf" not in terms["zn:" + name]
    assert profile["assignment"]["occupant_classes"] == ["Person", "Agent"]
    assert profile["seat"]["independent_of_occupant"] is True
    assert not any(profile["authority"].values())
    assert not any(profile["runtime"].values())
    assert profile["review_mapping"]["automatic_migration"] is False
    for case in profile["examples"]:
        assert (case["occupant_class"] in profile["assignment"]["occupant_classes"]) == case[
            "accepted"
        ]
    assert terms["zn:SUGGESTS_CREDENTIAL_PRESET"]["domain"] == "zn:SeatType"
    assert terms["zn:HAS_SEAT_OCCUPANT"]["range"] == "zn:Actor"


def test_seat_publication_preserves_all_runtime_contracts_and_prior_release():
    old = ONTOLOGY / "releases/v0.9.0"
    for name in [
        "operations.json",
        "arena-contract.json",
        "repository-contract.json",
        "zenith-local-space.json",
    ]:
        prior = json.loads((old / name).read_text())
        current = json.loads((ONTOLOGY / name).read_text())
        assert prior.pop("ontology_version") == "0.9.0"
        assert current.pop("ontology_version") == "0.10.0"
        assert current == prior
    assert (old / "constraints.cypher").read_bytes() == (
        ONTOLOGY / "neo4j/constraints.cypher"
    ).read_bytes()
    current = json.loads((ONTOLOGY / "ontology.jsonld").read_text())["@graph"]
    previous = json.loads((old / "ontology.jsonld").read_text())["@graph"]
    assert {x["@id"] for x in current if x.get("runtimeLabel")} == {
        x["@id"] for x in previous if x.get("runtimeLabel")
    }
    for root in [ONTOLOGY / "releases", ROOT / "src/devgraph/resources/ontology-releases"]:
        release = root / "v0.10.0"
        manifest = json.loads((release / "manifest.json").read_text())
        assert manifest["version"] == "0.10.0"
        for name in ["seats.md", "seats.json"]:
            assert (release / name).read_bytes() == (ONTOLOGY / name).read_bytes()
        for item in json.loads((root / "v0.9.0/manifest.json").read_text())["files"]:
            assert (
                hashlib.sha256((root / "v0.9.0" / item["path"]).read_bytes()).hexdigest()
                == item["sha256"]
            )
