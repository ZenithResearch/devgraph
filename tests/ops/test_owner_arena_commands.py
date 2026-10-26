import base64

import pytest
from tests.ops.test_owner_arena_upgrade import configured, signed_upgrade

from devgraph.auth.access_approval import DOMAIN
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.errors import UnauthenticatedError
from devgraph.ops.owner_arena_commands import OwnerArenaCommands
from devgraph.storage.memory import MemoryGraphStorage


def setup(tmp_path, monkeypatch):
    s, key, cfg, upgrade, request = configured(tmp_path, monkeypatch)
    request.update(arena_scope="all_current_and_future", create_arenas=True)
    signed_upgrade(upgrade, key, request)
    upgrade.apply(request["action_id"])
    upgrade.storage = MemoryGraphStorage()
    commands = OwnerArenaCommands(upgrade, AuditLog())
    owner = key.public_key().public_bytes_raw().hex()
    action = dict(
        schema="devgraph.access-operator-action.v11",
        action="create_arena",
        action_id="12" * 16,
        expected_policy_digest_sha256=upgrade.information(owner)["policy_digest_sha256"],
        request=dict(
            schema="devgraph.arena-request.v1",
            operation="create",
            kind="Arena",
            id="future-arena",
            expected_version=None,
            payload=dict(id="future-arena", title="Future Arena", description="Fixture only"),
        ),
    )
    return s, key, upgrade, commands, owner, action


def sign(key, challenge):
    return (
        base64.urlsafe_b64encode(key.sign(DOMAIN + canonical_json(challenge))).rstrip(b"=").decode()
    )


def test_owner_creation_uses_canonical_mutation_receipt_and_stable_retry(tmp_path, monkeypatch):
    _, key, upgrade, commands, owner, action = setup(tmp_path, monkeypatch)
    challenge = commands.challenge(owner, "34" * 16, action)
    result = commands.execute(challenge, sign(key, challenge))
    assert result["status"] == "created" and result["arena_id"] == "future-arena"
    assert upgrade.storage.get_node("Arena", "future-arena").properties["title"] == "Future Arena"
    repeat = commands.execute(challenge, sign(key, challenge))
    assert repeat["duplicate"] and repeat["receipt_id"] == result["receipt_id"]
    assert commands.audit.records[-1].operation == "devgraph.arena.create.v1"
    action = dict(
        schema="devgraph.access-operator-action.v11",
        action="arena_create_status",
        action_id="56" * 16,
        original_action_id=action["action_id"],
    )
    status = commands.status_challenge(owner, "34" * 16, action)
    assert (
        commands.status(status, sign(key, status))["result"]["receipt_id"] == result["receipt_id"]
    )


def test_wrong_owner_changed_request_and_invalid_signature_cannot_create(tmp_path, monkeypatch):
    _, key, upgrade, commands, owner, action = setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        commands.challenge("ef" * 32, "34" * 16, action)
    challenge = commands.challenge(owner, "34" * 16, action)
    with pytest.raises(UnauthenticatedError):
        commands.execute(challenge, "A" * 86)
    challenge["details"]["action"]["request"]["payload"]["title"] = "Changed"
    with pytest.raises(ValueError):
        commands.execute(challenge, sign(key, challenge))
    assert upgrade.storage.get_node("Arena", "future-arena") is None
