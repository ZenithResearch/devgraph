"""Public synthetic fixtures keep Rust requests aligned with the Python receiver."""

import json
from pathlib import Path

import pytest

from devgraph.arena_requests import ArenaRequest, InvalidArenaRequest
from devgraph.work_requests import InvalidWorkRequest, WorkRequest

FIXTURES = Path(__file__).resolve().parents[2] / "crates/devgraph-work-protocol/tests/fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def parse(raw):
    kind = json.loads(raw)["schema"]
    parser = ArenaRequest if kind == "devgraph.arena-request.v1" else WorkRequest
    return parser.from_json(raw.encode())


@pytest.mark.parametrize("case", load("requests.json") + load("arena-v1/requests.json"))
def test_shared_request_bytes_domains_and_resources(case):
    request = parse(case["raw"])
    assert request.canonical.decode() == case["canonical"]
    assert request.digest == case["digest"]
    assert request.authority_operation == case["operation"]
    assert list(request.resources) == case["resources"]


@pytest.mark.parametrize("case", load("adversarial.json"))
def test_shared_adversarial_decisions(case):
    raw = case["raw"].encode()
    if not case["python_accept"]:
        with pytest.raises(InvalidWorkRequest):
            WorkRequest.from_json(raw)
        return
    request = WorkRequest.from_json(raw)
    assert request.canonical.decode() == case["canonical"]
    assert request.digest == case["digest"]
    assert list(request.resources) == case["resources"]


@pytest.mark.parametrize("case", load("arena-v1/invalid-requests.json"))
def test_shared_arena_denials(case):
    with pytest.raises((InvalidArenaRequest, InvalidWorkRequest)):
        parse(case["raw"])


def test_public_request_fixture_copies_remain_identical():
    repository_fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    for public, shared in [
        ("named-work-v1/requests.json", "requests.json"),
        ("arena-v1/requests.json", "arena-v1/requests.json"),
        ("arena-v1/invalid-requests.json", "arena-v1/invalid-requests.json"),
    ]:
        assert (repository_fixtures / public).read_bytes() == (FIXTURES / shared).read_bytes()
