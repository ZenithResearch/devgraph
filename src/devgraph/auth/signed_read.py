"""Devgraph policy adapter for the shared, holder-bound secS read credential.

Membership eligibility and typed Arena constraints belong here. Cryptographic
issuance and verification use the matched secS binary, never a Python fallback.
The enrollment database supplies current status and an atomic replay ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.delegated_contract import WorkReadGrant, canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.native_read import RESOURCE, EnrollmentPolicy, NativeReadEnrollment, _seconds


class SecsReadAuthority:
    def __init__(self, *, binary: Path, policy_file: Path, issuer_key_file: Path):
        if not binary.is_absolute() or not binary.is_file():
            raise ValueError("matched secS read authority binary required")
        self.binary, self.policy_file, self.issuer_key_file = binary, policy_file, issuer_key_file
        self.policy()

    def policy(self) -> dict:
        info = self.policy_file.lstat()
        if (
            not self.policy_file.is_absolute()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > 65536
        ):
            raise ValueError("owner-private read policy required")
        return json.loads(self.policy_file.read_text())

    @staticmethod
    def policy_for(policy: EnrollmentPolicy, *, key_id: str, public_key: str) -> dict:
        return {
            "schema": "castalia.read-policy.v1",
            "issuer": policy.issuer,
            "key_id": key_id,
            "public_key": public_key,
            "receiver": policy.receiver,
            "audience": "devgraph",
            "max_lifetime_seconds": policy.lifetime_seconds,
            "permissions": [
                {
                    "operation": "devgraph.work." + operation,
                    "resource": RESOURCE,
                    "constraints": grant.model_dump(mode="json", exclude_defaults=True),
                }
                for grant in policy.grants
                for operation in grant.operations
            ],
        }

    def invoke_under_policy(self, policy: dict, operation: str, **fields) -> dict:
        # The application has already checked its typed narrowing rules against
        # the separately configured ceiling. Keep the resulting policy local,
        # private, and stable for the complete native invocation. Never accept a
        # policy from HTTP request fields. Canonicalize macOS temporary aliases.
        with tempfile.TemporaryDirectory(prefix="devgraph-read-policy-") as folder:
            path = Path(folder).resolve(strict=True) / "policy.json"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(canonical_json(policy))
                file.flush()
                os.fsync(file.fileno())
            return self._invoke(operation, path, **fields)

    def invoke(self, operation: str, **fields) -> dict:
        return self._invoke(operation, self.policy_file, **fields)

    def _invoke(self, operation: str, policy_file: Path, **fields) -> dict:
        args = [str(self.binary), "--policy-file", str(policy_file)]
        if operation == "issue":
            args += ["--issuer-key-file", str(self.issuer_key_file)]
        elif operation != "verify":
            raise UnauthenticatedError("unsupported_operation")
        try:
            result = subprocess.run(
                args,
                input=canonical_json({"operation": operation, **fields}),
                capture_output=True,
                timeout=5,
                check=True,
            )
            if len(result.stdout) > 65536:
                raise ValueError()
            value = json.loads(result.stdout)
            if value.get("ok") is not True or not isinstance(value.get("value"), dict):
                raise ValueError()
            return value["value"]
        except (OSError, ValueError, subprocess.SubprocessError):
            raise UnauthenticatedError("read_authority_denied") from None


class SignedReadEnrollment(NativeReadEnrollment):
    def __init__(self, *, authority: SecsReadAuthority, **kwargs):
        self.authority = authority
        super().__init__(**kwargs)
        self._authority_policy(self.policy())
        with self._database() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS read_replay (credential_id TEXT, nonce TEXT, "
                "expires INTEGER NOT NULL, PRIMARY KEY(credential_id,nonce))"
            )

    def _authority_policy(self, policy: EnrollmentPolicy) -> dict:
        configured = self.authority.policy()
        expected = self.authority.policy_for(
            policy, key_id=configured.get("key_id"), public_key=configured.get("public_key")
        )
        if configured != expected or policy.lifetime_seconds > 3600:
            raise UnauthenticatedError("read_policy_mismatch")
        return configured

    def _details(self, policy: EnrollmentPolicy) -> dict:
        return {
            **super()._details(policy),
            "schema": "devgraph.native-read-scope.v2",
            "authority_policy": self._authority_policy(policy),
        }

    def _invoke(self, policy: dict, operation: str, **fields) -> dict:
        return self.authority.invoke(operation, **fields)

    def _mint(self, manifest: dict, current: int) -> str:
        selected = self._policy_for(
            manifest["subject_public_key"], manifest["installation_id"], current
        )
        if selected.digest != manifest["policy_digest"]:
            raise UnauthenticatedError("policy_changed")
        policy = self._authority_policy(selected)
        manifest["credential_id"] = manifest["credential_id"].removeprefix("dgrn_")
        claims = {
            "schema": "castalia.read-credential.v1",
            "credential_id": manifest["credential_id"],
            "holder_public_key": manifest["subject_public_key"],
            "installation_id": manifest["installation_id"],
            "issuer": policy["issuer"],
            "key_id": policy["key_id"],
            "receiver": policy["receiver"],
            "audience": "devgraph",
            "policy_digest": hashlib.sha256(canonical_json(policy)).hexdigest(),
            "permissions": policy["permissions"],
            "issued_at": current,
            "expires_at": manifest["expires_at"],
        }
        credential = self._invoke(policy, "issue", claims=claims, now=current)
        return canonical_json(credential).decode()

    def _result(self, credential: str, manifest: dict) -> dict:
        return {
            "schema": "devgraph.native-read-result.v2",
            "credential": json.loads(credential),
            "manifest": manifest,
        }

    def verify(self, credential, *, audience, now=None):
        # A public credential alone is not a bearer credential, even if a caller
        # accidentally wires this class to a legacy Authorization adapter.
        raise UnauthenticatedError("holder_proof_required")

    def _manifest_current(self, manifest: dict, current: int) -> bool:
        return True

    def verify_request(
        self, *, credential: dict, call: dict, proof: dict, now: datetime | None = None
    ) -> AuthorityContext:
        current = _seconds(now)
        digest = hashlib.sha256(canonical_json(credential)).hexdigest()
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._check_policy_snapshot(db)
                row = db.execute(
                    "SELECT manifest,proof,status FROM credentials WHERE digest=?", (digest,)
                ).fetchone()
                if not row:
                    raise UnauthenticatedError("unknown_credential")
                manifest, membership = json.loads(row[0]), json.loads(row[1])
                policy = self._policy_for(
                    manifest["subject_public_key"], manifest["installation_id"], current
                )
                authority_policy = self._authority_policy(policy)
                active = (
                    row[2] == "active"
                    and self._manifest_current(manifest, current)
                    and manifest["policy_digest"] == policy.digest
                    and self.membership_check(membership, manifest["subject_public_key"])
                )
                verified = self._invoke(
                    authority_policy, "verify",
                    credential=credential,
                    call=call,
                    proof=proof,
                    active=active,
                    now=current,
                )
                # Only typed Work constraints from the verified operation become
                # selectors. Filtering still happens inside DelegatedReadService.
                grants = tuple(
                    WorkReadGrant.model_validate(p["constraints"]).model_copy(
                        update={"operations": (p["operation"].removeprefix("devgraph.work."),)}
                    )
                    for p in verified["permissions"]
                )
                if verified["credential_id"] != manifest["credential_id"]:
                    raise UnauthenticatedError("invalid_credential")
                db.execute("DELETE FROM read_replay WHERE expires <= ?", (current,))
                if db.execute("SELECT COUNT(*) FROM read_replay").fetchone()[0] >= 10000:
                    raise UnauthenticatedError("try_later")
                if db.execute(
                    "SELECT 1 FROM read_replay WHERE credential_id=? AND nonce=?",
                    (verified["credential_id"], proof["nonce"]),
                ).fetchone():
                    raise UnauthenticatedError("replay")
                db.execute(
                    "INSERT INTO read_replay VALUES(?,?,?)",
                    (verified["credential_id"], proof["nonce"], proof["issued_at"] + 60),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        identifier = verified["credential_id"]
        return AuthorityContext(
            envelope=CredentialEnvelope(
                actor_id=identifier,
                session_id=identifier,
                correlation_id=identifier,
                scopes=frozenset({"devgraph.work.read"}),
                expires_at=datetime.fromtimestamp(verified["expires_at"], timezone.utc),
                issuer=policy.issuer,
                audience="devgraph",
                credential_id=identifier,
                credential_version=2,
                read_grants=grants,
                lifecycle_status="active",
                resource=RESOURCE,
            )
        )
