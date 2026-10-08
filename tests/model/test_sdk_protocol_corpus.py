"""The SDK's checked byte fixtures remain aligned with the Python receiver."""

import json
from pathlib import Path

import pytest

from devgraph.work_requests import InvalidWorkRequest, WorkRequest

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/sdk-work-v1"


@pytest.mark.parametrize("case", json.loads((FIXTURES / "adversarial.json").read_text()))
def test_sdk_adversarial_contract(case):
    raw = case["raw"].encode("utf-8")
    if not case["python_accept"]:
        with pytest.raises(InvalidWorkRequest):
            WorkRequest.from_json(raw)
        return
    request = WorkRequest.from_json(raw)
    assert request.canonical.decode() == case["canonical"]
    assert request.digest == case["digest"]
    assert list(request.resources) == case["resources"]


def test_original_corpus_copies_do_not_drift():
    for name in ("requests.json", "signed-vectors.json"):
        assert (FIXTURES / name).read_bytes() == (
            Path(__file__).resolve().parents[1] / "fixtures/named-work-v1" / name
        ).read_bytes()
