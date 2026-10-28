"""Synthetic review-window tests. No Wallet approval or live graph mutation."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from devgraph.observability.session_actions import SessionStateActions

REF = "sr-" + "a" * 64


def fixture(tmp_path):
    canonical = {"id": REF, "canonical": True, "work_state": {"state": "unreviewed", "version": 1}}
    reader = SimpleNamespace(graph_reader=lambda _: canonical)
    launched = []
    bridge = SessionStateActions(
        root=tmp_path / "actions",
        python=Path(sys.executable),
        reader=reader,
        launch=launched.append,
    )
    return bridge, canonical, launched


def finish(command, *, code=0, ref=REF):
    folder = command.parent
    (folder / "exit-code").write_text(str(code))
    (folder / "result.json").write_text(
        json.dumps(
            {
                "receipt": {
                    "operation": "devgraph.work.session.state.v2",
                    "subject_label": "SessionRef",
                    "subject_id": ref,
                    "receipt_id": "synthetic-receipt-only",
                }
            }
        )
    )
    for path in (folder / "exit-code", folder / "result.json"):
        path.chmod(0o600)


def test_native_review_is_pending_until_receipt_and_fresh_canonical_read(tmp_path):
    bridge, canonical, launched = fixture(tmp_path)
    result = bridge.begin(REF, "done", 1)
    assert (
        result["phase"] == "awaiting_approval" and canonical["work_state"]["state"] == "unreviewed"
    )
    assert len(launched) == 1
    script = launched[0].read_text()
    assert " -I -m devgraph.observability.session_review --attempt-directory " in script
    assert "APPROVE" not in script and "2>" not in script and "< /dev/null" not in script
    assert launched[0].stat().st_mode & 0o077 == 0
    assert bridge.begin(REF, "done", 1)["ticket"] == result["ticket"] and len(launched) == 1
    finish(launched[0])
    assert bridge.status(result["ticket"])["phase"] == "unconfirmed"  # old canonical revision
    canonical["work_state"] = {"state": "done", "version": 2}
    saved = bridge.status(result["ticket"])
    assert saved["phase"] == "committed" and saved["work_state"] == canonical["work_state"]
    assert "request.json" not in json.dumps(saved) and str(tmp_path) not in json.dumps(saved)


def test_explicit_retry_keeps_original_request_and_idempotency(tmp_path):
    bridge, _, launched = fixture(tmp_path)
    result = bridge.begin(REF, "done", 1)
    root = bridge.root / result["ticket"]
    before = [(root / name).read_bytes() for name in ["request.json", "idempotency.txt"]]
    finish(launched[0], code=1)
    assert bridge.status(result["ticket"])["phase"] == "needs_review"
    bridge.begin(REF, "done", 1)
    assert len(launched) == 2
    assert before == [(root / name).read_bytes() for name in ["request.json", "idempotency.txt"]]


def test_stale_revision_invalid_ref_and_unknown_ticket_never_launch(tmp_path):
    bridge, _, launched = fixture(tmp_path)
    for ref, state, version in [
        (REF, "done", 2),
        (REF, "done", True),
        (REF, "ended", 1),
        ("../private", "done", 1),
    ]:
        with pytest.raises(ValueError):
            bridge.begin(ref, state, version)
    with pytest.raises(OSError):
        bridge.status("b" * 64)
    assert launched == [] and list(bridge.root.iterdir()) == []


def test_wrong_receipt_or_unavailable_canonical_read_cannot_claim_success(tmp_path):
    bridge, canonical, launched = fixture(tmp_path)
    result = bridge.begin(REF, "done", 1)
    canonical["work_state"] = {"state": "done", "version": 2}
    finish(launched[0], ref="sr-" + "b" * 64)
    assert bridge.status(result["ticket"])["phase"] == "needs_review"
    finish(launched[0])
    bridge.reader.graph_reader = lambda _: None
    assert bridge.status(result["ticket"])["phase"] == "unconfirmed"


def test_private_root_and_bundle_cannot_redirect(tmp_path):
    bridge, _, launched = fixture(tmp_path)
    result = bridge.begin(REF, "done", 1)
    request = bridge.root / result["ticket"] / "request.json"
    copy = request.with_name("original.json")
    request.rename(copy)
    request.symlink_to(copy)
    with pytest.raises(ValueError):
        bridge.status(result["ticket"])
    assert len(launched) == 1


def test_lost_response_and_abandoned_window_have_explicit_same_identity_recovery(tmp_path):
    bridge, canonical, launched = fixture(tmp_path)
    now = [100.0]
    bridge.clock = lambda: now[0]
    first = bridge.begin(REF, "done", 1)
    # Retain the historical non-gated attempt's bounded lease behavior.
    (launched[0].parent / "ready-gated").unlink()
    request_dir = bridge.root / first["ticket"]
    original = (request_dir / "idempotency.txt").read_bytes()
    # A lost HTTP response reuses the active review rather than opening another.
    assert bridge.begin(REF, "done", 1)["phase"] == "awaiting_approval"
    assert len(launched) == 1
    now[0] = 401.0
    assert bridge.status(first["ticket"])["phase"] == "unconfirmed"
    assert len(launched) == 1  # polling never launches a retry or claims Done
    second = bridge.begin(REF, "done", 1)  # explicit user retry after abandonment
    assert second["ticket"] == first["ticket"] and len(launched) == 2
    assert (request_dir / "idempotency.txt").read_bytes() == original
    assert canonical["work_state"]["state"] == "unreviewed"
    script = launched[0].read_text()
    assert "exec " in script


def test_committed_write_with_lost_response_can_reconcile_original_request(tmp_path):
    bridge, canonical, launched = fixture(tmp_path)
    result = bridge.begin(REF, "done", 1)
    request_dir = bridge.root / result["ticket"]
    original = (request_dir / "request.json").read_bytes()
    key = (request_dir / "idempotency.txt").read_bytes()
    finish(launched[0], code=1)  # CLI lost the HTTP response after commit
    canonical["work_state"] = {"state": "done", "version": 2}
    retry = bridge.begin(REF, "done", 1)  # the original browser intent remains reachable
    assert retry["phase"] == "needs_reconciliation" and retry["ticket"] == result["ticket"]
    bridge.reconcile(result["ticket"])
    assert len(launched) == 2
    assert (launched[-1].parent / "review-mode").read_text() == "reconcile"
    assert original == (request_dir / "request.json").read_bytes()
    assert key == (request_dir / "idempotency.txt").read_bytes()
    finish(launched[-1])
    assert bridge.status(result["ticket"])["phase"] == "committed"
    assert canonical["work_state"]["version"] == 2  # status lookup never advances state
