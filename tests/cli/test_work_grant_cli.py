from __future__ import annotations

import json

import pytest

from devgraph.cli import main
from devgraph.ops import work_grants


@pytest.mark.parametrize("action,function,extra", [
    ("status", "grant_status", []),
    ("apply", "apply_grant", ["--plan-file", "/private/reviewed-plan.json"]),
    ("renew", "renew_grant", []),
    ("rotate-verifier", "rotate_verifier", []),
])
@pytest.mark.parametrize("ready", [True, False])
def test_grant_exit_status_reports_admission(monkeypatch, capsys, action, function, extra, ready):
    monkeypatch.setattr(work_grants, function, lambda **kwargs: {"ready": ready})
    assert main(["auth", "work", action, *extra]) == (0 if ready else 2)
    assert json.loads(capsys.readouterr().out) == {"ready": ready}


@pytest.mark.parametrize("receiver,producer,expected", [
    (True, True, 0), (True, False, 2), (False, False, 2),
])
def test_incomplete_revocation_has_nonzero_exit(monkeypatch, receiver, producer, expected):
    monkeypatch.setattr(work_grants, "revoke_grant", lambda: {
        "ready": False, "receiver_revoked": receiver, "producer_revoked": producer,
    })
    assert main(["auth", "work", "revoke"]) == expected


def test_public_plan_does_not_need_ready_result(monkeypatch):
    monkeypatch.setattr(work_grants, "plan_grant", lambda **kwargs: {"schema": "plan"})
    assert main(["auth", "work", "plan"]) == 0
