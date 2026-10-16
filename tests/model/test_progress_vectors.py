import json
from pathlib import Path

import pytest

from devgraph.work_requests import InvalidWorkRequest, WorkRequest

ROOT = (
    Path(__file__).resolve().parents[2] / "crates/devgraph-work-protocol/tests/fixtures/progress-v2"
)


def test_python_published_progress_vectors():
    for case in json.loads((ROOT / "requests.json").read_text()):
        request = WorkRequest.from_json(case["raw"].encode())
        assert request.canonical.decode() == case["canonical"]
        assert request.digest == case["digest"]
        assert request.authority_operation == case["operation"]
        assert list(request.resources) == case["resources"]
    for case in json.loads((ROOT / "invalid-requests.json").read_text()):
        with pytest.raises(InvalidWorkRequest):
            WorkRequest.from_json(case["raw"].encode())
