"""Explicit test-only issuer + canonical receiver. Never imports local host config.

Create with a disposable Wallet PUBLIC key. Issuer preprovisions one synthetic
read grant; this module has no holder key, Wallet consent or production data.
It is outside the installed devgraph package and has no runtime fallback.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __name__ == "__main__":
    # Native consumers launch `python -I <reviewed script>` with no PYTHONPATH.
    # Load exactly this checkout's source, never the interpreter's editable sibling.
    source_root = Path(__file__).resolve().parents[2] / "src"
    bootstrap = source_root / "devgraph/_source_bootstrap.py"
    namespace = {"__name__": "_devgraph_source_bootstrap"}
    exec(compile(bootstrap.read_bytes(), str(bootstrap), "exec"), namespace)
    namespace["activate"](source_root)

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID

from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.holder_grants import HolderGrantHost
from devgraph.auth.holder_sessions import HolderSessionHost, HolderSessionPolicy
from devgraph.auth.monitor_read import (
    AccessDraftV2,
    MemberAccessPolicyV2,
    MemberMonitorReadEnrollment,
    compile_monitor_read,
)
from devgraph.auth.selector_grants import permission_v2
from devgraph.auth.signed_read import SecsReadAuthority
from devgraph.disposable_sessions import _write
from devgraph.holder_session_provider import HolderSessionApplication, serve
from devgraph.local_session_provider import checked_file, file_pin
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Task
from devgraph.signed_read_runtime import private_json
from devgraph.storage.memory import MemoryGraphStorage


def create_fixture(
    directory,
    *,
    holder_public_key,
    holder_binary,
    read_binary,
    origin="https://graph.example",
    maximum_seconds=300,
):
    if not re.fullmatch(r"[0-9a-f]{64}", holder_public_key):
        raise ValueError("public fixture holder required")
    policy = HolderSessionPolicy(
        enabled=True, allowed_origins=[origin], max_ttl_seconds=maximum_seconds
    )
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)
    directory = directory.resolve(strict=True)
    issuer = ed25519.Ed25519PrivateKey.generate()
    # Native authority's existing key reader consumes a private lowercase hex file.
    key = directory / "issuer.key"
    key.write_text(issuer.private_bytes_raw().hex())
    key.chmod(0o600)
    pin = {
        "issuer": "disposable-holder-session",
        "key_id": "fixture-key",
        "public_key": issuer.public_key().public_bytes_raw().hex(),
        "service": "urn:zenith:service:disposable-" + secrets.token_hex(16),
    }
    profile = {
        "schema": "devgraph.disposable-holder-session-provider.v1",
        "mode": "disposable",
        "provider": pin,
        "holder_public_key": holder_public_key,
        "created_at": int(time.time()),
        "context": {"origin": origin, "audience": "devgraph"},
        "policy": policy.model_dump(mode="json", by_alias=True),
        "holder_binary": file_pin(holder_binary),
        "read_binary": file_pin(read_binary),
    }
    _write(directory / "DISPOSABLE_HOLDER_SESSION_PROVIDER", {"service": pin["service"]})
    _write(directory / "profile.json", profile)
    app = fixture_application(directory / "profile.json")
    app.close()
    return directory / "profile.json"


def fixture_application(profile_file):
    profile_file = Path(profile_file).resolve(strict=True)
    directory = profile_file.parent
    profile = private_json(profile_file)
    if (
        set(profile)
        != {
            "schema",
            "mode",
            "provider",
            "holder_public_key",
            "created_at",
            "context",
            "policy",
            "holder_binary",
            "read_binary",
        }
        or profile["schema"] != "devgraph.disposable-holder-session-provider.v1"
        or profile["mode"] != "disposable"
        or private_json(directory / "DISPOSABLE_HOLDER_SESSION_PROVIDER")
        != {"service": profile["provider"]["service"]}
    ):
        raise ValueError("explicit disposable holder-session fixture required")
    holder_binary, read_binary = (
        checked_file(profile["holder_binary"]),
        checked_file(profile["read_binary"]),
    )
    pin, start = profile["provider"], profile["created_at"]
    # Installation is deliberately unbound in the issuer parent. The actual
    # approving Wallet installation is still signed into the session challenge.
    ceiling = AccessDraftV2.model_validate(
        {
            "schema": "devgraph.access-draft.v2",
            "issuer": pin["issuer"],
            "receiver": "https://fixture.invalid",
            "write_audience": "devgraph://receiver-local",
            "not_before": start - 1,
            "not_after": start + 86400,
            "lifetime_seconds": 300,
            "read_grants": [
                {
                    "scope": "devgraph.work.read",
                    "operations": ["get", "list"],
                    "relationship_types": ["parent"],
                    "work_kinds": ["Task"],
                    "work_ids": ["Task/allowed-1"],
                    "include_archived": False,
                }
            ],
            "write_grants": [],
        }
    )
    assignment = ceiling.model_copy(
        update={"subject_public_key": profile["holder_public_key"], "installation_id": "aa" * 16}
    )
    registry = MemberAccessPolicyV2.model_validate(
        {
            "schema": "devgraph.member-access-policy.v2",
            "revision": 1,
            "ceiling": ceiling,
            "assignments": [assignment],
        }
    )
    policy = compile_monitor_read(ceiling)
    authority_value = SecsReadAuthority.policy_for(
        policy, key_id=pin["key_id"], public_key=pin["public_key"]
    )
    authority_file = directory / "authority.json"
    if not authority_file.exists():
        _write(authority_file, authority_value)
    elif private_json(authority_file) != authority_value:
        raise ValueError("fixture authority changed")
    authority = SecsReadAuthority(
        binary=read_binary, policy_file=authority_file, issuer_key_file=directory / "issuer.key"
    )
    enrollment = MemberMonitorReadEnrollment(
        path=directory / "members.sqlite",
        policy=lambda: policy,
        membership_check=lambda proof, subject: proof == {},
        authority=authority,
        members=lambda: registry,
    )
    holder = HolderGrantHost(
        enrollment=enrollment,
        binary=holder_binary,
        binary_sha256=profile["holder_binary"]["sha256"],
        service=pin["service"],
        grant_schema="secs.holder-grant.v2",
    )
    if holder.pin != pin:
        raise ValueError("fixture pin changed")
    with holder._provider_context():
        with enrollment._database() as db:
            row = db.execute(
                "SELECT grant FROM holder_roots WHERE service=?", (pin["service"],)
            ).fetchone()
        if row is None:
            current = int(time.time())
            device = "aa" * 16  # Registry correlation only; parent installation is null.
            revision = holder.revision(profile["holder_public_key"], device, current)
            claims = {
                "schema": "secs.holder-grant.v2",
                "grant_id": secrets.token_hex(16),
                "revision": revision,
                **{k: pin[k] for k in ("issuer", "key_id", "service")},
                "holder_public_key": profile["holder_public_key"],
                "installation_id": None,
                "validity": {
                    "schema": "castalia.grant-validity.v1",
                    "mode": "dated",
                    "not_before": start - 1,
                    "not_after": start + 86400,
                },
                "max_credential_lifetime_seconds": 300,
                "onward_delegation": False,
                "permissions": [
                    permission_v2(p, holder.service)
                    for p in enrollment._authority_policy(policy)["permissions"]
                ],
            }
            parent = holder.invoke("issue_v2", claims=claims, now=current)
            manifest = {
                "subject_public_key": profile["holder_public_key"],
                "installation_id": device,
                "holder_revision": revision,
                "holder_request_digest": "00" * 32,
            }
            with enrollment._database() as db:
                db.execute("BEGIN IMMEDIATE")
                holder.retain(db, manifest, canonical_json(parent).decode(), {}, current)
                db.execute("COMMIT")
            _write(directory / "parent.json", parent)
    sessions = HolderSessionHost(holder, HolderSessionPolicy.model_validate(profile["policy"]))
    storage = MemoryGraphStorage()
    repository = WorkObjectRepository(storage)
    repository.create(Task(id="allowed-1", title="Disposable allowed task"))
    repository.create(Task(id="hidden", title="Outside the fixture grant"))

    def check():
        # Test-only revocation control. It is not an operation in either broker.
        if (directory / "REVOKE").exists():
            parent = private_json(directory / "parent.json")
            enrollment.revoke("dgrg_" + parent["claims"]["grant_id"])
        return private_json(profile_file) == profile

    return HolderSessionApplication(
        sessions=sessions,
        storage=storage,
        audit_log=AuditLog(),
        name="Disposable holder-session qualification",
        context=profile["context"],
        check=check,
    )


def tls_files(directory):
    import ipaddress

    directory = Path(directory)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    for path, raw in (
        (
            directory / "tls.key",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        ),
        (directory / "tls.crt", certificate.public_bytes(serialization.Encoding.PEM)),
    ):
        path.write_bytes(raw)
        path.chmod(0o600)
    return directory / "tls.crt", directory / "tls.key"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--holder-public-key")
    parser.add_argument("--holder-binary", type=Path)
    parser.add_argument("--read-binary", type=Path)
    parser.add_argument("--origin", default="https://graph.example")
    parser.add_argument("--profile-file", type=Path)
    parser.add_argument("--https", action="store_true")
    args = parser.parse_args()
    if args.profile_file is None:
        profile = create_fixture(
            args.directory,
            holder_public_key=args.holder_public_key,
            holder_binary=args.holder_binary,
            read_binary=args.read_binary,
            origin=args.origin,
        )
        print(
            json.dumps(
                {
                    "profile": str(profile),
                    "parent": str(profile.parent / "parent.json"),
                    "provider": private_json(profile)["provider"],
                    "context": private_json(profile)["context"],
                }
            ),
            flush=True,
        )
        return 0
    app = fixture_application(args.profile_file)
    if not args.https:
        return serve(app)
    import socket

    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from devgraph.api.holder_sessions import register_holder_sessions
    from devgraph.api.wallet_monitor import register_wallet_monitor
    from devgraph.auth.errors import UnauthenticatedError

    target = FastAPI()

    @target.exception_handler(UnauthenticatedError)
    async def denied(_request, _error):
        return JSONResponse({"error": "access_denied"}, status_code=401)

    @target.middleware("http")
    async def current(request, call_next):
        app.current()
        return await call_next(request)

    register_wallet_monitor(
        target, storage=app.storage, enrollment=app.sessions.host.enrollment, audit_log=app.audit
    )
    register_holder_sessions(target, host=app.sessions.host)
    cert, key = tls_files(args.profile_file.parent)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    print(
        json.dumps(
            {
                "base_url": "https://127.0.0.1:" + str(sock.getsockname()[1]),
                "certificate": str(cert),
            }
        ),
        flush=True,
    )
    try:
        uvicorn.Server(
            uvicorn.Config(target, log_level="error", ssl_keyfile=str(key), ssl_certfile=str(cert))
        ).run(sockets=[sock])
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
