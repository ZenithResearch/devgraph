"""Private Devgraph-owned browser transport to the fixed secS v2 producer.

This is not a Wallet/native-messaging adapter. Owner configuration admits exact
browser origins; secS independently checks caller grants and every signed input.
"""

from __future__ import annotations

import base64
import json
import re
import tempfile
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

from devgraph.auth.secs_issue_create import _canonical_json
from devgraph.auth.secs_work import SecSWorkDenied
from devgraph.credential_requests import credential_request_binding
from devgraph.named_requests import parse_named_request
from devgraph.ops.credential_work_agent import INSTALL_ROOT, SECS_BINARY, _run_adapter
from devgraph.ops.local_path_integrity import require_receiver_directory_path
from devgraph.ops.named_work_agent import _snapshot
from devgraph.ops.named_work_v2_receiver import BUNDLE
from devgraph.ops.secs_issue_create_receiver import _read_private_bounded_file, _strict_json_object
from devgraph.ops.secs_issue_create_wallet import (
    _close_wallet_binary_descriptors,
    _require_fixed_executable,
)


def browser_origin(value):
    if not isinstance(value, str) or len(value) > 200:
        return False
    parsed = urlsplit(value)
    return (
        value == f"{parsed.scheme}://{parsed.netloc}"
        and parsed.username is None
        and parsed.password is None
        and parsed.hostname is not None
        and (
            parsed.scheme == "https"
            or parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        )
    )


class BrowserCredentialWorkHost:
    def __init__(self, *, data_root, receiver):
        self.data_root, self.receiver = data_root, receiver

    def require_origin(self, origin):
        try:
            directory = require_receiver_directory_path(self.data_root, BUNDLE, missing_ok=False)
            config = _strict_json_object(
                _read_private_bounded_file(
                    directory / "browser.json",
                    label="browser credential transport",
                    maximum_bytes=8192,
                ),
                label="browser credential transport",
            )
            origins = config.get("origins")
            if (
                set(config) != {"schema", "origins"}
                or config["schema"] != "devgraph.browser-credential-transport.v2"
                or not isinstance(origins, list)
                or not 1 <= len(origins) <= 16
                or any(not browser_origin(item) for item in origins)
                or len(set(origins)) != len(origins)
                or not browser_origin(origin)
                or origin not in origins
            ):
                raise ValueError()
        except Exception:
            raise SecSWorkDenied("browser_origin_denied") from None

    def capabilities(self, *, value, origin):
        self.require_origin(origin)
        if value != {}:
            raise SecSWorkDenied()
        from devgraph.arena_requests import ARENA_OPERATIONS
        from devgraph.work_requests import WORK_OPERATIONS, WORK_OPERATIONS_V2

        return {
            "schema": "devgraph.credential-transport-capabilities.v2",
            "operations": [f"devgraph.work.{op}.v1" for op in WORK_OPERATIONS]
            + [f"devgraph.work.{op}.v2" for op in WORK_OPERATIONS_V2]
            + [f"devgraph.arena.{op}.v1" for op in ARENA_OPERATIONS],
        }

    def provider_profile(self, *, value, origin):
        """Propose public owner-configured trust; Wallet alone can approve it.

        This is not issuer discovery from an unverified operation credential.
        Pins must still match the receiver's current managed admission registry.
        """
        self.require_origin(origin)
        try:
            if value != {}:
                raise ValueError()
            directory = require_receiver_directory_path(self.data_root, BUNDLE, missing_ok=False)
            profile = _strict_json_object(
                _read_private_bounded_file(
                    directory / "wallet-provider.json",
                    label="Wallet provider",
                    maximum_bytes=65_536,
                ),
                label="Wallet provider",
            )
            if (
                set(profile) != {"schema", "display_name", "origins", "membership", "presentations"}
                or profile["schema"] != "castalia.provider-profile.v1"
                or profile["origins"] != [origin]
                or profile["membership"] is not None
                or not isinstance(profile["display_name"], str)
                or re.fullmatch(r"[ -~]{1,160}", profile["display_name"]) is None
                or not isinstance(profile["presentations"], list)
                or not 1 <= len(profile["presentations"]) <= 64
            ):
                raise ValueError()
            config = self.receiver._load_verifier().config
            now = config.clock()
            seen = set()
            for pin in profile["presentations"]:
                if (
                    not isinstance(pin, dict)
                    or set(pin) != {"issuer", "key_id", "public_key", "audience", "callers"}
                    or any(
                        not isinstance(pin[field], str)
                        or re.fullmatch(r"[ -~]{1,200}", pin[field]) is None
                        for field in ("issuer", "key_id", "audience")
                    )
                    or pin["audience"] != config.audience
                    or pin["callers"] != [{"kind": "browser", "id": origin}]
                ):
                    raise ValueError()
                key = config.key_registry.require_production_key(pin["key_id"], now=now)
                if pin["public_key"] != key.public_key.hex():
                    raise ValueError()
                identity = (pin["issuer"], pin["key_id"], pin["audience"])
                if identity in seen:
                    raise ValueError()
                seen.add(identity)
            return profile
        except Exception:
            raise SecSWorkDenied("wallet_provider_setup_unavailable") from None

    def _produce(self, action, value, key):
        with ExitStack() as stack:
            path = INSTALL_ROOT / SECS_BINARY
            try:
                directories, binary = _require_fixed_executable(
                    path, expected_path=path, install_root=INSTALL_ROOT, relative_path=SECS_BINARY
                )
            except Exception:
                raise SecSWorkDenied("credential_producer_unavailable") from None
            stack.callback(_close_wallet_binary_descriptors, directories, binary)
            root = Path(
                stack.enter_context(tempfile.TemporaryDirectory(prefix="devgraph-browser-v2-"))
            )
            root.chmod(0o700)
            source, keyfile, output = root / "input.json", root / "key.txt", root / "output.json"
            _snapshot(source, _canonical_json(value))
            _snapshot(keyfile, (key + "\n").encode("ascii"))
            flag = (
                "--presentation-request-output"
                if action == "issue-credential"
                else "--signed-projection-output"
            )
            try:
                result = _run_adapter(
                    [
                        str(path),
                        action,
                        "--request-file",
                        str(source),
                        "--idempotency-key-file",
                        str(keyfile),
                        flag,
                        str(output),
                    ],
                    env={},
                )
                if result.returncode != 0:
                    raise ValueError()
                return _read_private_bounded_file(
                    output,
                    label="credential producer result",
                    maximum_bytes=262_144 if action == "issue-credential" else 16_384,
                )
            except Exception:
                raise SecSWorkDenied("credential_producer_denied") from None

    def _request(self, value):
        if not isinstance(value.get("request"), str):
            raise SecSWorkDenied()
        key = value.get("idempotency_key")
        if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9._~-]{16,128}", key) is None:
            raise SecSWorkDenied()
        request = parse_named_request(value["request"].encode("utf-8"))
        return request, key, credential_request_binding(request.canonical, key)

    def prepare(self, *, value, origin):
        self.require_origin(origin)
        if set(value) != {"request", "idempotency_key", "holder_public_key", "caller"}:
            raise SecSWorkDenied()
        caller = {"kind": "browser", "id": origin}
        if (
            value["caller"] != caller
            or re.fullmatch(r"[0-9a-f]{64}", str(value["holder_public_key"])) is None
        ):
            raise SecSWorkDenied()
        request, key, binding = self._request(value)
        raw = self._produce(
            "issue-credential",
            {
                "schema": "secs-devgraph-credential-input.v2",
                "schema_version": 2,
                "request": json.loads(request.canonical),
                "holder_public_key": value["holder_public_key"],
                "caller": caller,
            },
            key,
        )
        prepared = _strict_json_object(raw, label="presentation request")
        if (
            set(prepared) != {"schema", "request_bytes_base64", "credential", "disclosure"}
            or prepared["schema"] != "castalia.credential-presentation-request.v2"
            or prepared["disclosure"] != binding.disclosure
            or prepared["request_bytes_base64"] != base64.b64encode(binding.request_bytes).decode()
        ):
            raise SecSWorkDenied("credential_request_binding_mismatch")
        return prepared

    def authorize(self, *, value, origin):
        self.require_origin(origin)
        if set(value) != {"request", "idempotency_key", "credential", "disclosure", "presentation"}:
            raise SecSWorkDenied()
        caller = {"kind": "browser", "id": origin}
        try:
            if (
                value["credential"]["claims"]["caller"] != caller
                or value["presentation"]["caller"] != caller
            ):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise SecSWorkDenied("browser_caller_mismatch") from None
        request, key, binding = self._request(value)
        if value["disclosure"] != binding.disclosure:
            raise SecSWorkDenied("credential_disclosure_mismatch")
        projection = self._produce(
            "authorize",
            {
                "schema": "secs-devgraph-work-producer-input.v2",
                "schema_version": 2,
                "request": json.loads(request.canonical),
                "credential": value["credential"],
                "disclosure": value["disclosure"],
                "presentation": value["presentation"],
            },
            key,
        )
        return request.canonical, projection, key

    def execute(self, *, value, origin):
        raw, projection, key = self.authorize(value=value, origin=origin)
        return self.receiver.execute(
            request_json=raw, projection_json=projection, idempotency_key=key
        )

    def status(self, *, value, origin):
        raw, projection, key = self.authorize(value=value, origin=origin)
        return self.receiver.status(
            request_json=raw, projection_json=projection, idempotency_key=key
        )
