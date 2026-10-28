"""Explicit disposable qualification host, never the installed/private graph.

This module creates only a NEW empty owner-private directory, fixture issuer and
in-memory Work. It neither reads local.json nor connects to Neo4j or a real Wallet.
No runtime path calls it automatically. Private stdio is broker-internal; owner
signatures are still required and are never manufactured by this host.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import selectors
import subprocess
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devgraph.auth.access_operator import AccessOperator, OperatorPolicy
from devgraph.auth.access_provisioning import ReadGrantProvisioning
from devgraph.auth.access_requests import AccessRequestInbox, RequestAction
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.holder_grants import HolderGrantHost, checksum_bytes
from devgraph.auth.monitor_read import (
    AccessDraftV2,
    MemberAccessPolicyV2,
    MemberMonitorReadEnrollment,
    compile_monitor_read,
)
from devgraph.auth.session_access import SessionAccessProvider
from devgraph.auth.signed_read import SecsReadAuthority
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Task
from devgraph.signed_read_runtime import private_json
from devgraph.storage.memory import MemoryGraphStorage


def _write(path, value):
    raw = canonical_json(value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())


def create_disposable_profile(
    directory, owner_public_key, *, session_binary, holder_binary, read_binary
):
    """Developer/test setup only. Creates an issuer for fixture data, not grants."""
    from devgraph.session_review_contract import _text

    _text(owner_public_key, r"[0-9a-f]{64}")
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)  # exclusive: never repurpose existing state
    directory = directory.resolve(strict=True)
    issuer = Ed25519PrivateKey.generate()
    key = directory / "fixture-issuer.key"
    fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(issuer.private_bytes_raw().hex())
    binaries = {
        name: dict(
            path=str(Path(path).resolve(strict=True)),
            sha256=checksum_bytes(Path(path).resolve(strict=True)),
        )
        for name, path in dict(
            session=session_binary, holder=holder_binary, read=read_binary
        ).items()
    }
    value = dict(
        schema="devgraph.disposable-session-provider.v1",
        mode="disposable",
        service="urn:zenith:service:disposable-" + secrets.token_hex(16),
        receiver="https://session-fixture.invalid",
        issuer="disposable-session-provider",
        key_id="disposable-session-key",
        public_key=issuer.public_key().public_bytes_raw().hex(),
        operator_public_key=owner_public_key,
        created_at=int(time.time()),
        binaries=binaries,
    )
    _write(directory / "DISPOSABLE_SESSION_PROVIDER", dict(service=value["service"]))
    _write(directory / "profile.json", value)
    return directory / "profile.json"


class NativeSessionProcess:
    """One private inherited pipe; keys stay in the child, never in this class."""

    def __init__(self, binary, profile, *, timeout_seconds=10):
        if not 0 < timeout_seconds <= 30:
            raise ValueError("invalid_custody_timeout")
        self.timeout_seconds = timeout_seconds
        self.child = subprocess.Popen(
            [binary, "--profile-file", str(profile)],
            env={},
            cwd="/",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        os.set_blocking(self.child.stdin.fileno(), False)
        os.set_blocking(self.child.stdout.fileno(), False)

    def _exchange(self, raw):
        deadline = time.monotonic() + self.timeout_seconds
        response = bytearray()
        with selectors.DefaultSelector() as poller:
            poller.register(self.child.stdin, selectors.EVENT_WRITE)
            while raw:
                if not poller.select(max(0, deadline - time.monotonic())):
                    raise TimeoutError()
                try:
                    written = os.write(self.child.stdin.fileno(), raw)
                except BlockingIOError:
                    continue
                if written <= 0:
                    raise BrokenPipeError()
                raw = raw[written:]
            poller.unregister(self.child.stdin)
            poller.register(self.child.stdout, selectors.EVENT_READ)
            while True:
                if not poller.select(max(0, deadline - time.monotonic())):
                    raise TimeoutError()
                try:
                    chunk = os.read(self.child.stdout.fileno(), 131073 - len(response))
                except BlockingIOError:
                    continue
                if not chunk:
                    raise BrokenPipeError()
                response.extend(chunk)
                if len(response) > 131072:
                    raise ValueError("session_response_capacity")
                if b"\n" in response:
                    if not response.endswith(b"\n") or response.count(b"\n") != 1:
                        raise ValueError("session_response_framing")
                    return bytes(response)

    def call(self, operation, **fields):
        if self.child.poll() is not None:
            raise RuntimeError("session_custody_unavailable")
        request = dict(
            schema="castalia.session-agent-command.v1",
            request_id=secrets.token_hex(16),
            body=dict(operation=operation, **fields),
        )
        raw = canonical_json(request) + b"\n"
        if len(raw) > 131072:
            raise ValueError("session_command_capacity")
        from devgraph.ops.secs_issue_create_receiver import _strict_json_object

        try:
            raw = self._exchange(raw)
            result = _strict_json_object(raw, label="session helper result")
        except (OSError, ValueError, TimeoutError):
            self.close()
            raise RuntimeError("session_custody_unavailable") from None
        if (
            result.get("schema") != "castalia.session-agent-result.v1"
            or result.get("ok") is not True
            or result.get("request_id") != request["request_id"]
            or not isinstance(result.get("value"), dict)
        ):
            raise RuntimeError("session_custody_denied")
        return result["value"]

    def close(self):
        if self.child.poll() is None:
            self.child.terminate()
        try:
            self.child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=1)
        self.child.stdin.close()
        self.child.stdout.close()


class DisposableSessionApplication:
    """Fixed configured service plus real native custody/crypto, fixture data only."""

    def __init__(self, profile_file):
        profile_file = Path(profile_file).resolve(strict=True)
        profile = private_json(profile_file)
        if (
            set(profile)
            != {
                "schema",
                "mode",
                "service",
                "receiver",
                "issuer",
                "key_id",
                "public_key",
                "operator_public_key",
                "created_at",
                "binaries",
            }
            or profile["schema"] != "devgraph.disposable-session-provider.v1"
            or profile["mode"] != "disposable"
        ):
            raise ValueError("disposable_profile_required")
        directory = profile_file.parent
        if private_json(directory / "DISPOSABLE_SESSION_PROVIDER") != {
            "service": profile["service"]
        }:
            raise ValueError("disposable_marker_required")
        for value in profile["binaries"].values():
            if (
                set(value) != {"path", "sha256"}
                or checksum_bytes(Path(value["path"])) != value["sha256"]
            ):
                raise ValueError("native_binary_changed")
        self.profile, self.directory = profile, directory
        self.clients = {}
        start = profile["created_at"]
        self.inbox = AccessRequestInbox(
            path=directory / "requests.sqlite",
            issuer=profile["issuer"],
            receiver=profile["receiver"],
            membership_check=lambda proof, subject: proof == {},
        )
        ceiling = AccessDraftV2.model_validate(
            dict(
                schema="devgraph.access-draft.v2",
                issuer=profile["issuer"],
                receiver=profile["receiver"],
                write_audience="devgraph://receiver-local",
                not_before=start - 1,
                not_after=start + 86400,
                lifetime_seconds=60,
                write_grants=[],
                read_grants=[
                    dict(
                        scope="devgraph.work.read",
                        operations=["get", "list"],
                        relationship_types=["parent"],
                        work_kinds=["Task"],
                        include_archived=False,
                    )
                ],
            )
        )
        base = MemberAccessPolicyV2.model_validate(
            dict(
                schema="devgraph.member-access-policy.v2",
                revision=1,
                ceiling=ceiling,
                assignments=[],
            )
        )
        self.policy = OperatorPolicy.model_validate(
            dict(
                schema="devgraph.access-operator-policy.v1",
                revision=1,
                operator_public_keys=[profile["operator_public_key"]],
                ceiling=ceiling,
            )
        )
        provisioning = ReadGrantProvisioning(inbox=self.inbox, members=lambda: base)
        self.operator = AccessOperator(
            inbox=self.inbox, policy=lambda: self.policy, provisioning=provisioning
        )
        read_policy = compile_monitor_read(ceiling)
        authority_value = SecsReadAuthority.policy_for(
            read_policy, key_id=profile["key_id"], public_key=profile["public_key"]
        )
        authority_path = directory / "authority.json"
        if not authority_path.exists():
            _write(authority_path, authority_value)
        elif private_json(authority_path) != authority_value:
            raise ValueError("disposable_authority_changed")
        authority = SecsReadAuthority(
            binary=Path(profile["binaries"]["read"]["path"]),
            policy_file=authority_path,
            issuer_key_file=directory / "fixture-issuer.key",
        )
        enrollment = MemberMonitorReadEnrollment(
            path=directory / "enrollment.sqlite",
            policy=lambda: read_policy,
            membership_check=lambda proof, subject: proof == {},
            authority=authority,
            members=provisioning.registry,
        )
        self.holder = HolderGrantHost(
            enrollment=enrollment,
            binary=Path(profile["binaries"]["holder"]["path"]),
            binary_sha256=profile["binaries"]["holder"]["sha256"],
            service=profile["service"],
            grant_schema="secs.holder-grant.v2",
        )
        digest = hashlib.sha256(canonical_json(profile)).hexdigest()
        self.provider = SessionAccessProvider(
            operator=self.operator, holder=self.holder, profile_digest=digest
        )
        self.receiver_pin = dict(
            issuer=profile["issuer"],
            key_id=profile["key_id"],
            public_key=profile["public_key"],
            receiver=profile["receiver"],
            audience="devgraph",
        )
        self.storage, self.audit = MemoryGraphStorage(), AuditLog()
        repository = WorkObjectRepository(self.storage)
        repository.create(Task(id="example-task", title="Disposable example task"))
        repository.create(Task(id="hidden-task", title="Outside the fixture grant"))

    def _client(self, client):
        if client not in self.clients:
            raise RuntimeError("session_custody_unavailable")
        return self.clients[client]

    def _preparation(self, review):
        return dict(
            review=review,
            capabilities=self.provider.capabilities(),
            trusted_provider=self.provider.capabilities()["provider"],
            holder=review["manifest"]["holder"],
            receiver_pin=self.receiver_pin,
        )

    def prepare(self, client, request):
        if not isinstance(client, str) or not re.fullmatch(r"[0-9a-f]{32}", client):
            raise ValueError("invalid_session_client")
        base_fields = {"service", "request_id", "permissions", "display"}
        if set(request) not in (base_fields | {"expires_at"}, base_fields | {"lifetime_seconds"}):
            raise ValueError("invalid_session_request")
        if request["service"] != self.profile["service"]:
            raise ValueError("unknown_session_service")
        if (
            not isinstance(request["request_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", request["request_id"])
            or type(request.get("expires_at", request.get("lifetime_seconds"))) is not int
            or not isinstance(request["permissions"], list)
            or not 1 <= len(request["permissions"]) <= 16
        ):
            raise ValueError("invalid_session_request")
        with self.inbox._database() as db:
            prior = db.execute(
                "SELECT 1 FROM access_requests WHERE id=?", (request["request_id"],)
            ).fetchone()
        if prior and (
            client not in self.clients
            or request["request_id"] not in self.clients[client]["identities"]
        ):
            raise RuntimeError("session_custody_unavailable")
        if client not in self.clients:
            if len(self.clients) >= 8:
                raise ValueError("session_client_capacity")
            pinfile = self.directory / ("helper-" + client + ".json")
            value = dict(
                schema="castalia.session-agent-profile.v1",
                provider=self.holder.pin,
                receiver=self.receiver_pin,
                maximum_session_seconds=3720,
            )
            if not pinfile.exists():
                _write(pinfile, value)
            elif private_json(pinfile) != value:
                raise ValueError("session_profile_changed")
            native = NativeSessionProcess(self.profile["binaries"]["session"]["path"], pinfile)
            self.clients[client] = dict(native=native, identities={})
        context = self._client(client)
        if request["request_id"] not in context["identities"]:
            # Custody has a bounded preparation window, not extra grant time.
            # The issuer still anchors at most3600sec to the final review start.
            public = context["native"].call("create_session", expires_at=int(time.time()) + 3720)
            context["identities"][request["request_id"]] = dict(
                session_handle=public["session_handle"],
                public_key=public["holder_public_key"],
                installation_id=public["installation_id"],
            )
        identity = context["identities"][request["request_id"]]
        action = RequestAction.model_validate(
            dict(
                schema="devgraph.access-request-action.v1",
                action="submit",
                request_id=request["request_id"],
                subject_public_key=identity["public_key"],
                installation_id=identity["installation_id"],
                intent=None,
            )
        )
        challenge = self.inbox.wallet_challenge(action)
        proof = context["native"].call(
            "approve_member_request", session_handle=identity["session_handle"], challenge=challenge
        )
        self.inbox.execute_wallet(proof["challenge"], {}, proof["signature"])
        review = self.provider.prepare(
            client,
            request["request_id"],
            identity,
            request["permissions"],
            request.get("expires_at"),
            request["display"],
            lifetime_seconds=request.get("lifetime_seconds"),
        )
        return self._preparation(review)

    def submit(self, client, handle, proof):
        if set(proof) != {"challenge", "signature"}:
            raise ValueError("invalid_owner_proof")
        context = self._client(client)
        result = self.provider.submit(client, handle, **proof)
        if result["state"] == "submitting":
            challenge = self.provider.begin_enrollment(client, handle)
            with self.inbox._database() as db:
                identity = json.loads(self.provider._row(db, client, handle)[3])["manifest"][
                    "holder"
                ]
            approval = context["native"].call(
                "approve_enrollment", session_handle=identity["session_handle"], challenge=challenge
            )
            self.provider.complete_enrollment(
                client, handle, approval["challenge"], approval["signature"]
            )
        return self.reconcile(client, handle)

    def reconcile(self, client, handle):
        context = self._client(client)  # never generate a key on recovery
        if context["native"].child.poll() is not None:
            raise RuntimeError("session_custody_unavailable")
        result = self.provider.reconcile(client, handle)
        if result["state"] == "issued":
            root = self.provider.reconcile(client, handle, internal_root=True)
            with self.inbox._database() as db:
                review = json.loads(self.provider._row(db, client, handle)[3])
            context["native"].call(
                "install_grant",
                session_handle=review["manifest"]["holder"]["session_handle"],
                grant=root["grant"],
                reviewed_expiry=review["manifest"]["expires_at"],
                reviewed_permissions=review["manifest"]["permissions"],
            )
        return result

    def execute(self, client, credential_handle, request_id, call):
        context = self._client(client)
        with self.inbox._database() as db:
            candidates = db.execute(
                "SELECT handle,original_root FROM session_access_reviews "
                "WHERE client=? AND state='issued'",
                (client,),
            ).fetchall()
        handle = next(
            (
                handle
                for handle, original in candidates
                if original and "root_" + json.loads(original)["id"] == credential_handle
            ),
            None,
        )
        if not handle:
            raise ValueError("session_credential_unavailable")
        with self.inbox._database() as db:
            identity = json.loads(self.provider._row(db, client, handle)[3])["manifest"]["holder"]
        envelope = context["native"].call(
            "present_call", session_handle=identity["session_handle"], call=call
        )
        return self.provider.execute(
            client, handle, request_id, envelope, storage=self.storage, audit_log=self.audit
        )

    def close(self):
        for context in self.clients.values():
            context["native"].close()
