"""Reload owner-controlled named Work trust at each authorization decision."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from devgraph.auth.secs_issue_create import (
    SecSIssueCreateDenied,
    SecSIssueCreatePolicyBinding,
    SecSIssueCreatePolicyRegistry,
    SecSIssueCreateVerifierConfig,
    SecSVerifierKeyRegistry,
)
from devgraph.auth.secs_work import SecSWorkAdapter, SecSWorkDenied
from devgraph.auth.secs_work_v2 import SecSWorkV2Verifier
from devgraph.ops.local_path_integrity import (
    LocalPathIntegrityError,
    require_receiver_directory_path,
)
from devgraph.ops.named_work_receiver import BUNDLE as MANAGED_BUNDLE
from devgraph.ops.named_work_receiver import LocalNamedWorkReceiver
from devgraph.ops.secs_issue_create_receiver import (
    LocalSecSIssueCreateError,
    _read_private_bounded_file,
    _strict_json_object,
)

BUNDLE = Path("secrets/secs-magik/devgraph.work.v2")


class _CurrentReceiverVerifier:
    """Re-read admission pins at each verification, including after lock wait."""

    def __init__(self, load: Callable[[], SecSWorkV2Verifier]):
        self._load = load

    def verify(self, *, request_json: bytes, projection_json: bytes, idempotency_key: str):
        return self._load().verify(
            request_json=request_json,
            projection_json=projection_json,
            idempotency_key=idempotency_key,
        )


class LocalNamedWorkV2Receiver:
    def __init__(self, *, data_root, storage, audit_log, clock=None):
        self.data_root, self.storage, self.audit_log = data_root, storage, audit_log
        self.clock = clock or (lambda: int(time.time()))

    def _load_verifier(self) -> SecSWorkV2Verifier:
        try:
            bundle = require_receiver_directory_path(self.data_root, BUNDLE, missing_ok=False)
            if bundle is None:
                raise ValueError()
            manifest = _strict_json_object(
                _read_private_bounded_file(
                    bundle / "receiver.json",
                    label="named Work receiver",
                    maximum_bytes=16_384,
                ),
                label="named Work receiver",
            )
            if (
                set(manifest)
                != {"schema", "schema_version", "audience", "stable_issuer", "policy_binding"}
                or manifest["schema"] != "devgraph-secs-work-receiver.v2"
                or type(manifest["schema_version"]) is not int
                or manifest["schema_version"] != 2
                or manifest["audience"] != "devgraph://receiver-local"
                or not isinstance(manifest["policy_binding"], dict)
                or set(manifest["policy_binding"])
                != {"policy_id", "policy_version", "policy_digest_sha256"}
            ):
                raise ValueError()
            # The existing grant administrator revokes the managed v1 admission
            # bundle first. V2 activation cannot outlive those current pins.
            managed = (
                LocalNamedWorkReceiver(
                    data_root=self.data_root,
                    storage=self.storage,
                    audit_log=self.audit_log,
                    clock=self.clock,
                )
                ._load_verifier()
                .config
            )
            managed.policy_registry.require_binding(**manifest["policy_binding"])
            managed_bundle = require_receiver_directory_path(
                self.data_root, MANAGED_BUNDLE, missing_ok=False
            )
            managed_registry = _strict_json_object(
                _read_private_bounded_file(
                    managed_bundle / "secs-public-key-registry.json",
                    label="managed verifier registry",
                    maximum_bytes=262_144,
                ),
                label="managed verifier registry",
            )
            activated_registry_bytes = _read_private_bounded_file(
                bundle / "secs-public-key-registry.json",
                label="activated verifier registry",
                maximum_bytes=262_144,
            )
            activated_registry = _strict_json_object(
                activated_registry_bytes, label="activated verifier registry"
            )
            if (
                managed.audience != manifest["audience"]
                or managed.stable_issuer != manifest["stable_issuer"]
                or managed_registry != activated_registry
            ):
                raise ValueError()
            config = SecSIssueCreateVerifierConfig(
                audience=manifest["audience"],
                stable_issuer=manifest["stable_issuer"],
                policy_registry=SecSIssueCreatePolicyRegistry(
                    [SecSIssueCreatePolicyBinding(**manifest["policy_binding"])]
                ),
                key_registry=SecSVerifierKeyRegistry.from_json(activated_registry_bytes),
                clock=self.clock,
            )
        except (
            SecSIssueCreateDenied,
            LocalPathIntegrityError,
            LocalSecSIssueCreateError,
            ValueError,
            TypeError,
            KeyError,
        ):
            raise SecSWorkDenied("named_work_receiver_unavailable") from None
        return SecSWorkV2Verifier(config)

    def execute(self, *, request_json: bytes, projection_json: bytes, idempotency_key: str):
        return SecSWorkAdapter(
            storage=self.storage,
            audit_log=self.audit_log,
            verifier=_CurrentReceiverVerifier(self._load_verifier),
        ).execute(
            request_json=request_json,
            projection_json=projection_json,
            idempotency_key=idempotency_key,
        )

    def status(self, *, request_json: bytes, projection_json: bytes, idempotency_key: str):
        """Return only this holder's matching receipt; absence remains unknown."""
        from devgraph.events.outbox import (
            EventOutbox,
            _require_matching_scope,
            digest_idempotency_claim,
        )

        with self.storage.work_mutation_transaction():
            verified = self._load_verifier().verify(
                request_json=request_json,
                projection_json=projection_json,
                idempotency_key=idempotency_key,
            )
            request = verified.request
            claim = digest_idempotency_claim(verified.principal, idempotency_key)
            receipt = EventOutbox(self.storage)._find_receipt_by_idempotency_digest(
                "idempotency_claim_digest", claim
            )
            if receipt is not None:
                kind, work_id = (
                    ("Issue", request.payload["issue_id"])
                    if request.operation == "convert"
                    else (request.kind, request.id)
                )
                _require_matching_scope(
                    receipt,
                    operation=request.authority_operation,
                    subject_label=kind,
                    subject_id=work_id,
                    request_digest_sha256=request.digest,
                )
            return receipt
