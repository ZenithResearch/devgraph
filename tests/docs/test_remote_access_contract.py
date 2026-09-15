from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import get_args

from devgraph.remote.contracts import REMOTE_ENVELOPE_ADAPTER, RemoteOperation
from devgraph.remote.responses import (
    RemoteFound,
    RemoteListed,
    RemoteMutationAccepted,
    RemoteProblemResponse,
)

ROOT = Path(__file__).resolve().parents[2]
REMOTE_DOC = ROOT / "docs" / "dev" / "remote-access.md"


def test_documented_commands_validate_against_the_public_envelope() -> None:
    examples = re.findall(r"```json\n(.*?)\n```", REMOTE_DOC.read_text(), re.DOTALL)
    envelopes = [REMOTE_ENVELOPE_ADAPTER.validate_python(json.loads(item)) for item in examples]

    assert len(envelopes) == len(get_args(RemoteOperation))
    assert {envelope.operation for envelope in envelopes} == set(get_args(RemoteOperation))
    for example, envelope in zip(examples, envelopes, strict=True):
        assert set(json.loads(example)) == {"operation", "arguments"}
        assert envelope.model_dump(mode="json") == json.loads(example)


def test_documented_response_allowlists_match_public_models() -> None:
    text = REMOTE_DOC.read_text()
    for model in (RemoteFound, RemoteListed, RemoteMutationAccepted, RemoteProblemResponse):
        row = re.search(rf"^\| `{model.__name__}` \| (.*?) \|$", text, re.MULTILINE)
        assert row is not None, model.__name__
        assert set(re.findall(r"`([^`]+)`", row.group(1))) == set(model.model_fields)


def test_documented_smoke_targets_exist_and_require_no_listener() -> None:
    commands = re.findall(r"```bash\n(.*?)\n```", REMOTE_DOC.read_text(), re.DOTALL)
    assert len(commands) == 1
    argv = shlex.split(commands[0])
    assert argv[:4] == ["PYTHONPATH=src:tests/integration", "python", "-m", "pytest"]
    assert set(argv[4:-1]) == {
        "tests/remote",
        "tests/integration/test_remote_authority_flow.py",
        "tests/integration/test_remote_local_read_boundary.py",
        "tests/docs/test_remote_access_contract.py",
    }
    assert argv[-1] == "-q"
    assert all((ROOT / target).exists() for target in argv[4:-1])


def test_documentation_separates_legacy_fixture_from_signed_write_contract() -> None:
    text = REMOTE_DOC.read_text()
    section = text.split("## Current signed-write incompatibility\n", 1)[1].split("\n## ", 1)[0]
    for contract in (
        "DevgraphWorkContext",
        "execute_named_work",
        "POST /work-operations/v1",
        "X-Devgraph-Work-Authority",
        "expected_version",
        "[A-Za-z0-9._~-]{16,128}",
        "canonical request digest",
        "secS projection",
    ):
        assert contract in section
    assert "Production remote mutations must remain disabled" in section
    assert "retry the identical request and key" in section
    assert "local read credential cannot authorize" in section


def test_documentation_marks_draft_scope_and_synthetic_acceptance() -> None:
    text = REMOTE_DOC.read_text()
    for boundary in (
        "Status: draft library adapter; not deployed remote access",
        "Signed-write adoption is an explicit draft blocker",
        "MemoryGraphStorage",
        "LocalDevVerifier",
        "TestClient",
        "in-process library proof only",
        "unknown outcome",
    ):
        assert boundary in text


def test_documentation_links_only_to_public_checkout_material() -> None:
    text = REMOTE_DOC.read_text()
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
    assert links
    for link in links:
        assert "://" not in link
        target = (REMOTE_DOC.parent / link.split("#", 1)[0]).resolve()
        assert target.is_relative_to(ROOT)
        assert target.is_file(), link
        assert not target.is_relative_to(ROOT / "docs" / "issues")
        assert not target.is_relative_to(ROOT / "docs" / "reviews")
