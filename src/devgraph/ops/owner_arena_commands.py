"""Exact owner Arena commands over protected local secS RPC.

The native gate verifies Wallet signatures before dispatch. This receiver then
rechecks the active registered owner, the explicitly signed create-Arena role,
current policy digest, payload and proof lifetime before and inside the canonical
mutation transaction. It uses the normal mutation/audit/outbox/idempotency path;
no bearer, self-issued member credential or localhost exception is introduced.
"""

import hashlib
import secrets
from typing import Literal

from pydantic import Field

from devgraph.arena_requests import ArenaRequest
from devgraph.auth.access_approval import WalletAccessChallenge, verify_wallet_approval
from devgraph.auth.access_policy import _Closed
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.native_read import _seconds
from devgraph.auth.secs_work import SecSWorkAdapter, _Principal, _VerifiedWork
from devgraph.events.outbox import EventOutbox, digest_idempotency_claim
from devgraph.ops import owner_install as files
from devgraph.signed_read_runtime import owner_operator_policy, private_json


class OwnerArenaCreate(_Closed):
    schema_: Literal["devgraph.access-operator-action.v11"] = Field(alias="schema")
    action: Literal["create_arena"]
    action_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_policy_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request: dict


def request_for(action):
    request = ArenaRequest.from_json(canonical_json(action["request"]))
    if request.operation != "create" or request.kind != "Arena":
        raise ValueError("exact Arena creation required")
    return request


class OwnerArenaCommands:
    def __init__(self, upgrade, audit):
        self.upgrade, self.audit = upgrade, audit

    def binding(self, owner):
        cfg, _, review = self.upgrade._binding()
        if not cfg.owner_arena_administration or cfg.owner_arena_upgrade_file is None:
            raise ValueError("explicit owner Arena role required")
        policy = owner_operator_policy(cfg, data_root=self.upgrade.data_root)
        proof = private_json(cfg.owner_arena_upgrade_file, maximum_bytes=262144)
        if (
            owner != review["owner_public_key"]
            or proof["challenge"]["details"]["action"].get("create_arenas") is not True
        ):
            raise ValueError("owner Arena creation not authorized")
        from devgraph.auth.access_policy import _draft_validity
        from devgraph.auth.arena_access import OwnerArenaOperatorPolicy

        if not _draft_validity(OwnerArenaOperatorPolicy.model_validate(policy).ceiling).contains(
            _seconds(None)
        ):
            raise ValueError("owner Arena role expired")
        return cfg, policy, review

    def directory(self, action_id):
        # Reuse the exact identifier and protected parent validation.
        return self.upgrade._directory(action_id).parent.parent / "owner-arena-commands" / action_id

    def challenge(self, owner, device, action):
        action = OwnerArenaCreate.model_validate(action)
        raw = action.model_dump(mode="json", by_alias=True)
        request_for(raw)
        _, policy, _ = self.binding(owner)
        from devgraph.auth.arena_access import OwnerArenaOperatorPolicy

        current_policy = OwnerArenaOperatorPolicy.model_validate(policy)
        if action.expected_policy_digest_sha256 != current_policy.digest:
            raise ValueError("owner policy changed")
        current = _seconds(None)
        value = dict(
            schema="castalia.access-request-challenge.v1",
            challenge_id=secrets.token_hex(16),
            nonce=secrets.token_urlsafe(32),
            issuer=current_policy.ceiling.issuer,
            receiver=current_policy.ceiling.receiver,
            subject_public_key=owner,
            installation_id=device,
            operation="access.decide",
            issued_at=current,
            expires_at=current + 120,
            details=dict(
                action=raw,
                policy_revision=current_policy.revision,
                policy_digest_sha256=current_policy.digest,
            ),
        )
        WalletAccessChallenge.model_validate(value)
        directory = self.directory(action.action_id)
        if directory.exists():
            stored = private_json(directory / "challenge.json")
            if (
                stored["details"]["action"] != raw
                or stored["subject_public_key"] != owner
                or stored["installation_id"] != device
            ):
                raise ValueError("original Arena command changed")
            if current >= stored["expires_at"]:
                raise ValueError("original Arena command expired; inspect result")
            return stored
        files.save(directory / "challenge.json", canonical_json(value))
        return value

    def principal(self, challenge):
        return _Principal(
            issuer=challenge["issuer"],
            audience="devgraph://receiver-local",
            actor_id="pubkey:sha256:"
            + hashlib.sha256(bytes.fromhex(challenge["subject_public_key"])).hexdigest(),
            session_id=challenge["installation_id"],
            correlation_id=challenge["details"]["action"]["action_id"],
        )

    def status_challenge(self, owner, device, action):
        import re

        if (
            set(action) != {"schema", "action", "action_id", "original_action_id"}
            or action["schema"] != "devgraph.access-operator-action.v11"
            or action["action"] != "arena_create_status"
            or any(
                not re.fullmatch("[0-9a-f]{32}", action[k])
                for k in ("action_id", "original_action_id")
            )
        ):
            raise ValueError("exact Arena status action required")
        self.binding(owner)
        original = private_json(self.directory(action["original_action_id"]) / "challenge.json")
        if original["subject_public_key"] != owner or original["installation_id"] != device:
            raise ValueError("original owner installation required")
        current = _seconds(None)
        value = dict(
            original,
            challenge_id=secrets.token_hex(16),
            nonce=secrets.token_urlsafe(32),
            operation="access.status",
            issued_at=current,
            expires_at=current + 120,
            details=dict(original["details"], action=action),
        )
        WalletAccessChallenge.model_validate(value)
        return value

    def execute(self, challenge, signature):
        action = OwnerArenaCreate.model_validate(challenge["details"]["action"])
        raw = action.model_dump(mode="json", by_alias=True)
        directory = self.directory(action.action_id)
        if private_json(directory / "challenge.json") != challenge:
            raise ValueError("original Arena command required")
        request = request_for(raw)
        command = self

        class Verifier:
            def verify(self, *, request_json, projection_json, idempotency_key):
                _, policy, _ = command.binding(challenge["subject_public_key"])
                from devgraph.auth.arena_access import OwnerArenaOperatorPolicy

                policy = OwnerArenaOperatorPolicy.model_validate(policy)
                parsed = verify_wallet_approval(
                    challenge,
                    signature,
                    issuer=policy.ceiling.issuer,
                    receiver=policy.ceiling.receiver,
                    current=_seconds(None),
                )
                if (
                    parsed.operation != "access.decide"
                    or policy.digest != action.expected_policy_digest_sha256
                    or request_json != request.canonical
                    or idempotency_key != action.action_id
                    or projection_json
                    != canonical_json(dict(challenge=challenge, signature=signature))
                ):
                    raise ValueError("Arena command binding changed")
                return _VerifiedWork(
                    request=request,
                    principal=command.principal(challenge),
                    idempotency_digest=hashlib.sha256(idempotency_key.encode("ascii")).hexdigest(),
                    safe_summary=dict(
                        authorization="registered-owner-arena-command-v1",
                        request_digest_sha256=request.digest,
                    ),
                )

        adapter = SecSWorkAdapter(
            storage=self.upgrade.storage, verifier=Verifier(), audit_log=self.audit
        )
        arena, receipt, duplicate = adapter.execute(
            request_json=request.canonical,
            projection_json=canonical_json(dict(challenge=challenge, signature=signature)),
            idempotency_key=action.action_id,
        )
        return dict(
            schema="devgraph.access-operator-result.v2",
            action_id=action.action_id,
            authority_issued=False,
            status="created",
            arena_id=request.id,
            request_digest_sha256=request.digest,
            receipt_id=receipt.id,
            duplicate=duplicate,
        )

    def status(self, challenge, signature):
        action = challenge["details"]["action"]
        original = private_json(self.directory(action["original_action_id"]) / "challenge.json")
        _, policy, _ = self.binding(challenge["subject_public_key"])
        verify_wallet_approval(
            challenge,
            signature,
            issuer=original["issuer"],
            receiver=original["receiver"],
            current=_seconds(None),
        )
        if (
            challenge["operation"] != "access.status"
            or challenge["installation_id"] != original["installation_id"]
        ):
            raise ValueError("original command status proof required")
        request = request_for(original["details"]["action"])
        receipt = EventOutbox(self.upgrade.storage)._find_receipt_by_idempotency_digest(
            "idempotency_claim_digest",
            digest_idempotency_claim(self.principal(original), action["original_action_id"]),
        )
        if receipt is None:
            raise ValueError("Arena creation result not yet confirmed")
        if receipt.request_digest_sha256 != request.digest:
            raise ValueError("Arena receipt changed")
        return dict(
            schema="devgraph.access-operator-result.v2",
            action_id=action["action_id"],
            original_action_id=action["original_action_id"],
            authority_issued=False,
            status="reconciled",
            result=dict(
                schema="devgraph.access-operator-result.v2",
                action_id=action["original_action_id"],
                authority_issued=False,
                status="created",
                arena_id=request.id,
                request_digest_sha256=request.digest,
                receipt_id=receipt.id,
                duplicate=True,
            ),
        )
