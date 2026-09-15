"""Reload owner-controlled named Work trust at each authorization decision."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from devgraph.auth.secs_issue_create import (
    SecSIssueCreatePolicyBinding,
    SecSIssueCreatePolicyRegistry,
    SecSIssueCreateVerifierConfig,
    SecSVerifierKeyRegistry,
)
from devgraph.auth.secs_work import SecSWorkAdapter, SecSWorkDenied, SecSWorkVerifier
from devgraph.ops.local_path_integrity import (
    LocalPathIntegrityError,
    require_receiver_directory_path,
)
from devgraph.ops.secs_issue_create_receiver import (
    LocalSecSIssueCreateError,
    _read_private_bounded_file,
    _strict_json_object,
)

BUNDLE = Path("secrets/secs-magik/devgraph.work.v1")


class _CurrentReceiverVerifier:
    """Re-read admission pins at each verification, including after lock wait."""

    def __init__(self, load: Callable[[], SecSWorkVerifier]):
        self._load = load

    def verify(self, *, request_json: bytes, projection_json: bytes, idempotency_key: str):
        return self._load().verify(
            request_json=request_json, projection_json=projection_json,
            idempotency_key=idempotency_key,
        )


class LocalNamedWorkReceiver:
    def __init__(self, *, data_root, storage, audit_log, clock=None):
        self.data_root, self.storage, self.audit_log = data_root, storage, audit_log
        self.clock = clock or (lambda: int(time.time()))

    def _load_verifier(self) -> SecSWorkVerifier:
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
                or manifest["schema"] != "devgraph-secs-work-receiver.v1"
                or type(manifest["schema_version"]) is not int
                or manifest["schema_version"] != 1
                or manifest["audience"] != "devgraph://receiver-local"
                or not isinstance(manifest["policy_binding"], dict)
                or set(manifest["policy_binding"])
                != {"policy_id", "policy_version", "policy_digest_sha256"}
            ):
                raise ValueError()
            config = SecSIssueCreateVerifierConfig(
                audience=manifest["audience"],
                stable_issuer=manifest["stable_issuer"],
                policy_registry=SecSIssueCreatePolicyRegistry(
                    [SecSIssueCreatePolicyBinding(**manifest["policy_binding"])]
                ),
                key_registry=SecSVerifierKeyRegistry.from_json(
                    _read_private_bounded_file(
                        bundle / "secs-public-key-registry.json",
                        label="named Work verifier registry",
                        maximum_bytes=262_144,
                    )
                ),
                clock=self.clock,
            )
        except (
            LocalPathIntegrityError,
            LocalSecSIssueCreateError,
            ValueError,
            TypeError,
            KeyError,
        ):
            raise SecSWorkDenied("named_work_receiver_unavailable") from None
        return SecSWorkVerifier(config)

    def execute(self, *, request_json: bytes, projection_json: bytes, idempotency_key: str):
        return SecSWorkAdapter(
            storage=self.storage, audit_log=self.audit_log,
            verifier=_CurrentReceiverVerifier(self._load_verifier),
        ).execute(
            request_json=request_json,
            projection_json=projection_json,
            idempotency_key=idempotency_key,
        )
