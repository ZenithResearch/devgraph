"""Opt-in Rust core -> native enrollment -> guarded Python receiver qualification.

All identities and credentials are disposable and remain in process/temp storage.
This is source integration, not installed iPhone or legacy signing-chain evidence.
"""

import base64
import hashlib
import json
import os
import subprocess

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import build_services

from devgraph.api.native_read import create_native_read_app
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.native_membership import ZenithMembershipValidator
from devgraph.auth.native_read import EnrollmentPolicy, NativeReadEnrollment


@pytest.mark.parametrize("signed", [False, True])
def test_actual_rust_approval_enrollment_and_recovery_excludes_access(tmp_path, signed):
    driver = os.environ.get("CASTALIA_RECOVERY_TEST_DRIVER")
    if not driver:
        pytest.skip("set CASTALIA_RECOVERY_TEST_DRIVER to the built disposable test driver")
    process = subprocess.Popen([driver], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def call(operation, **fields):
        process.stdin.write(json.dumps({"operation": operation, **fields}) + "\n")
        process.stdin.flush()
        reply = json.loads(process.stdout.readline())
        assert reply["ok"], reply.get("error")
        return reply["value"]

    def commit(preview):
        candidate = call(
            "commit_candidate",
            token=preview["token"],
            expected_revision=preview["expected_revision"],
        )
        return call("confirm_commit", token=preview["token"], snapshot=candidate["snapshot"])

    def reject(operation, **fields):
        process.stdin.write(json.dumps({"operation": operation, **fields}) + "\n")
        process.stdin.flush()
        assert not json.loads(process.stdout.readline())["ok"]

    try:
        commit(
            call(
                "prepare_create", password="test local password", created_at=1, expected_revision=0
            )
        )
        backup = call("export_recovery", password="test backup password")["file"]
        commit(call("verify_backup", file=backup, password="test backup password"))
        owner = call("identity")["ownerPublicKey"]
        issuer = Ed25519PrivateKey.generate()
        trust = {
            "v3": {
                "schema": "castalia.zenith-membership-trust-policy.v1",
                "version": 1,
                "roots": [
                    {
                        "issuerId": "fixture",
                        "keyId": "fixture",
                        "signatureSuite": "Ed25519",
                        "publicKey": issuer.public_key().public_bytes_raw().hex(),
                    }
                ],
            }
        }
        commit(call("prepare_trust", trust=trust))
        proof = {
            "schema": "castalia.zenith-membership-credential.v3",
            "version": 3,
            "ownerPublicKey": owner,
            "membershipId": hashlib.sha256(
                b"castalia/zenith-membership-id/v3\0" + bytes.fromhex(owner)
            ).hexdigest(),
            "status": "active",
            "issuerId": "fixture",
            "issuerKeyId": "fixture",
            "signatureSuite": "Ed25519",
        }
        transcript = (
            b"castalia/zenith-membership-credential/v3\0"
            + (3).to_bytes(8, "little")
            + bytes.fromhex(proof["membershipId"])
            + bytes.fromhex(owner)
        )
        for part in ("active", "fixture", "fixture", "ed25519"):
            transcript += len(part).to_bytes(4, "little") + part.encode()
        proof["issuerSignature"] = (
            base64.urlsafe_b64encode(issuer.sign(transcript)).rstrip(b"=").decode()
        )
        commit(call("activate_membership", proof=proof, scoped=False))
        policy = EnrollmentPolicy.model_validate(
            {
                "schema": "devgraph.native-read-policy.v1",
                "issuer": "fixture",
                "receiver": "https://example.test",
                "lifetime_seconds": 3600,
                "grants": [
                    {
                        "scope": "devgraph.work.read",
                        "operations": ["list", "get"],
                        "relationship_types": ["parent"],
                        "work_kinds": ["Task"],
                        "include_archived": False,
                    }
                ],
            }
        )
        enrollment = NativeReadEnrollment(
            path=tmp_path / "native.sqlite",
            policy=lambda: policy,
            membership_check=ZenithMembershipValidator(lambda: trust, lambda _: False),
        )
        prefix = "/devgraph/native-read"
        factory = create_native_read_app
        if signed:
            from pathlib import Path

            from devgraph.api.signed_read import create_signed_read_app
            from devgraph.auth.signed_read import SecsReadAuthority, SignedReadEnrollment

            binary = os.environ.get("SECS_READ_AUTHORITY_TEST_BINARY")
            assert binary, "matched secS binary required for signed core qualification"
            signing = Ed25519PrivateKey.generate()
            key_file = tmp_path / "issuer.key"
            key_file.write_text(signing.private_bytes_raw().hex())
            key_file.chmod(0o600)
            config = SecsReadAuthority.policy_for(
                policy,
                key_id="fixture-read",
                public_key=signing.public_key().public_bytes_raw().hex(),
            )
            policy_file = tmp_path / "read-policy.json"
            policy_file.write_text(json.dumps(config))
            policy_file.chmod(0o600)
            enrollment = SignedReadEnrollment(
                path=tmp_path / "signed.sqlite",
                policy=lambda: policy,
                membership_check=enrollment.membership_check,
                authority=SecsReadAuthority(
                    binary=Path(binary), policy_file=policy_file, issuer_key_file=key_file
                ),
            )
            prefix += "/v2"
            factory = create_signed_read_app
        services = build_services(frozenset())
        http = TestClient(
            factory(storage=services.storage, enrollment=enrollment, audit_log=AuditLog())
        )
        challenge = http.post(prefix + "/challenges", json=call("read_access_identity")).json()
        if signed:
            pin = {k: config[k] for k in ("issuer", "key_id", "public_key", "receiver", "audience")}
            reject(
                "approve_read_credential",
                challenge=json.dumps(challenge),
                pin={**pin, "public_key": "00" * 32},
                now=challenge["issued_at"],
            )
            approved = call(
                "approve_read_credential",
                challenge=json.dumps(challenge),
                pin=pin,
                now=challenge["issued_at"],
            )
        else:
            approved = call(
                "approve_read_access",
                challenge=json.dumps(challenge),
                receiver=policy.receiver,
                now=challenge["issued_at"],
            )
        issued = http.post(prefix + "/enrollments", json=approved)
        assert issued.status_code == 200, issued.text
        result = issued.json()
        if signed:
            # A response cannot silently substitute another credential or expand
            # the request the user confirmed. Failure consumes only the preview.
            changed = json.loads(json.dumps(result["credential"]))
            changed["claims"]["expires_at"] += 1
            reject(
                "store_read_credential",
                request_id=challenge["request_id"],
                credential=changed,
                now=challenge["issued_at"],
            )
            call(
                "approve_read_credential",
                challenge=json.dumps(challenge),
                pin=pin,
                now=challenge["issued_at"],
            )
            commit(
                call(
                    "store_read_credential",
                    request_id=challenge["request_id"],
                    credential=result["credential"],
                    now=challenge["issued_at"],
                )
            )
            commit(call("prepare_trust", trust=trust))
            held = call("read_credential", receiver=policy.receiver, now=challenge["issued_at"])
            assert held["credential"] == result["credential"]
            read_call = {
                "operation": "devgraph.work.list",
                "resource": "https://work.zenith-research.ca/devgraph",
                "parameters": {"kind": "Task", "limit": 20},
            }
            signed_request = call(
                "sign_read_call",
                receiver=policy.receiver,
                call=read_call,
                now=challenge["issued_at"],
            )
            again = call(
                "sign_read_call",
                receiver=policy.receiver,
                call=read_call,
                now=challenge["issued_at"],
            )
            assert again["proof"]["nonce"] != signed_request["proof"]["nonce"]
            reject(
                "sign_read_call",
                receiver=policy.receiver,
                now=challenge["issued_at"],
                call={**read_call, "operation": "devgraph.work.delete"},
            )
            response = http.post(prefix + "/requests", json=signed_request)
            assert http.post(prefix + "/requests", json=signed_request).status_code == 401
            assert (
                http.get(
                    "/devgraph/work/Task",
                    headers={"Authorization": "Bearer " + json.dumps(result["credential"])},
                ).status_code
                == 404
            )
        else:
            commit(
                call(
                    "store_read_access",
                    request_id=challenge["request_id"],
                    secret=result["credential"],
                    expires_at=result["manifest"]["expires_at"],
                    metadata=result["manifest"],
                    now=challenge["issued_at"],
                )
            )
            commit(call("prepare_trust", trust=trust))
            held = call("read_access", receiver=policy.receiver, now=challenge["issued_at"])
            assert held["secret"] == result["credential"]
            response = http.get(
                "/devgraph/work/Task", headers={"Authorization": "Bearer " + held["secret"]}
            )
        assert response.status_code == 200, response.text
        data = call("export_data", password="test data password", exported_at=1)["file"]
        assert str(result["credential"]) not in data and str(result["credential"]) not in backup
        call("lock")
        # New session models a clean installation. Restore proof data, then activate
        # explicitly: even that cannot reconstruct the previous read credential.
        process.stdin.close()
        process.wait(timeout=5)
        process = subprocess.Popen(
            [driver], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        call("approve_restore_trust", trust=trust)
        commit(
            call(
                "prepare_restore",
                file=backup,
                file_password="test backup password",
                password="other local password",
                expected_revision=0,
                data_file=data,
                data_password="test data password",
            )
        )
        assert not call("state")["membership_active"]
        commit(call("activate_membership", proof=proof, scoped=False))
        process.stdin.write(
            json.dumps(
                {
                    "operation": "read_credential" if signed else "read_access",
                    "receiver": policy.receiver,
                    "now": challenge["issued_at"],
                }
            )
            + "\n"
        )
        process.stdin.flush()
        assert not json.loads(process.stdout.readline())["ok"]
    finally:
        process.stdin.close()
        process.wait(timeout=5)
