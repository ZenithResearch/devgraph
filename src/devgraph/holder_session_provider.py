"""Private local holder-session adapter; application owns temporary key custody.

The broker's client_id is correlation only. Transfer must bind opaque handles
and helper processes to its authenticated local clients. This process has no
key import, signer, custody handle, owner fallback, renewal or remote target.
"""

from __future__ import annotations

import json
import re
import sys

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from devgraph.api.signed_read import execute_signed_read
from devgraph.api.wallet_monitor import wallet_monitor_read
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.holder_session_transport import loads


class HolderSessionApplication:
    def __init__(self, *, sessions, storage, audit_log, name, context, now=None, check=None):
        sessions._enabled()
        if (
            set(context) != {"origin", "audience"}
            or context["origin"] not in sessions.policy.allowed_origins
            or context["audience"] != sessions.policy.audience
            or not isinstance(name, str)
            or not 1 <= len(name) <= 80
        ):
            raise ValueError("invalid_holder_session_provider")
        self.sessions, self.storage, self.audit = sessions, storage, audit_log
        self.name, self.context, self.now, self.check = name, dict(context), now, check

    def current(self):
        self.sessions._enabled()
        if self.check is not None and not self.check():
            raise ValueError("holder_session_profile_changed")

    def clock(self):
        return None if self.now is None else self.now()

    def _bound(self, value):
        if any(value.get(key) != expected for key, expected in self.context.items()):
            raise ValueError("holder_session_context_denied")

    def services(self):
        return {
            "schema": "devgraph.holder-session-services.v1",
            "services": [
                {
                    "service": self.sessions.host.service,
                    "name": self.name,
                    "issuer_pin": self.sessions.host.pin,
                    "context": self.context,
                    "grant_schemas": ["secs.holder-grant.v2"],
                    "read_only": True,
                    "max_ttl_seconds": self.sessions.policy.max_ttl_seconds,
                }
            ],
        }

    def prepare(self, body):
        self._bound(body)
        return self.sessions.prepare(body, now=self.clock())

    def exchange(self, authorization):
        self._bound(authorization["challenge"])
        return self.sessions.exchange(authorization, now=self.clock())

    def status(self, proof):
        return self.sessions.status(proof, now=self.clock(), expected_context=self.context)

    def execute(self, presentation):
        if set(presentation) != {"credential", "call", "proof"}:
            raise ValueError("invalid_holder_session_presentation")
        self._bound(presentation["credential"]["claims"]["challenge"])
        call = presentation["call"]
        with self.sessions.from_origin(self.context["origin"]):
            if set(call.get("parameters", {})) == {"path"}:
                result = wallet_monitor_read(
                    self.storage, self.sessions.host.enrollment, self.audit, presentation
                )
            else:
                result = execute_signed_read(
                    storage=self.storage,
                    enrollment=self.sessions.host.enrollment,
                    audit_log=self.audit,
                    body=presentation,
                )
        if isinstance(result, JSONResponse):
            if result.status_code >= 400:
                raise ValueError("holder_session_read_unavailable")
            result = json.loads(result.body)
        return {
            "schema": "devgraph.holder-session-call-result.v1",
            "service": self.sessions.host.service,
            "operation": call["operation"],
            "result": jsonable_encoder(result),
        }

    def close(self):
        close = getattr(self.storage, "close", None)
        if close is not None:
            close()


def dispatch(app, value):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "request_id", "client_id", "operation", "arguments"}
        or value["schema"] != "devgraph.holder-session-provider-command.v1"
        or any(
            not isinstance(value[k], str) or not re.fullmatch(r"[0-9a-f]{32}", value[k])
            for k in ("request_id", "client_id")
        )
        or not isinstance(value["arguments"], dict)
    ):
        raise ValueError("invalid_holder_session_provider_command")
    fields = {
        "services": set(),
        "prepare": {"body"},
        "exchange": {"authorization"},
        "status": {"proof"},
        "execute": {"presentation"},
    }
    operation, args = value["operation"], value["arguments"]
    if not isinstance(operation, str) or operation not in fields or set(args) != fields[operation]:
        raise ValueError("unsupported_holder_session_provider_command")
    app.current()
    result = getattr(app, operation)(**args)
    app.current()
    if len(canonical_json(result)) > 262144:
        raise ValueError("holder_session_provider_result_capacity")
    return result


def serve(app, *, source=None, sink=None):
    """Private inherited NDJSON pipe. EOF closes composition, never grants."""
    source = sys.stdin.buffer if source is None else source
    sink = sys.stdout.buffer if sink is None else sink
    try:
        while True:
            raw = source.readline(262145)
            if not raw:
                return 0
            if len(raw) > 262144 or not raw.endswith(b"\n"):
                return 2
            request_id = None
            try:
                value = loads(raw, maximum_bytes=262144)
                candidate = value.get("request_id")
                if isinstance(candidate, str) and re.fullmatch(r"[0-9a-f]{32}", candidate):
                    request_id = candidate
                result = {
                    "schema": "devgraph.holder-session-provider-result.v1",
                    "request_id": request_id,
                    "ok": True,
                    "value": dispatch(app, value),
                }
                if len(canonical_json(result)) > 262144:
                    raise ValueError("result capacity")
            except Exception:
                # Exchange may already have committed. Only original-key status
                # can resolve that outcome; never leak raw errors/private bytes.
                result = {
                    "schema": "devgraph.holder-session-provider-result.v1",
                    "request_id": request_id,
                    "ok": False,
                    "error": "holder_session_provider_denied_or_outcome_unknown",
                }
            sink.write(canonical_json(result) + b"\n")
            sink.flush()
    finally:
        app.close()


def main(argv=None):
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Private finite holder-session provider")
    parser.add_argument("--profile-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        from devgraph.local_holder_session_provider import LocalHolderSessionApplication

        app = LocalHolderSessionApplication(args.profile_file)
    except Exception:
        print("holder_session_provider_profile_unavailable", file=sys.stderr)
        return 2
    return serve(app)
