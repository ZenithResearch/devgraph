"""Long-lived private process wire for disposable session qualification only."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from devgraph.auth.delegated_contract import canonical_json
from devgraph.disposable_sessions import DisposableSessionApplication
from devgraph.ops.secs_issue_create_receiver import _strict_json_object

MAX = 262144


def dispatch(app, value):
    if (
        set(value) != {"schema", "request_id", "client_id", "operation", "arguments"}
        or value["schema"] != "devgraph.session-provider-command.v1"
        or any(
            not isinstance(value[k], str) or not re.fullmatch(r"[0-9a-f]{32}", value[k])
            for k in ("request_id", "client_id")
        )
        or not isinstance(value["arguments"], dict)
    ):
        raise ValueError("invalid_provider_command")
    client, action, args = value["client_id"], value["operation"], value["arguments"]
    fields = {
        "services": set(),
        "close_client": set(),
        "prepare": {"service", "request_id", "permissions", "expires_at", "display"},
        "update_expiry": {"request_handle", "expires_at"},
        "owner_challenge": {"request_handle", "owner_public_key", "installation_id"},
        "submit": {"request_handle", "owner_proof"},
        "reconcile": {"request_handle"},
        "execute": {"credential_handle", "request_id", "call"},
    }
    valid = action in fields and set(args) == fields[action]
    if action == "prepare":
        valid = set(args) in (
            fields[action],
            (fields[action] - {"expires_at"}) | {"lifetime_seconds"},
        )
    if not valid:
        raise ValueError("unsupported_provider_command")
    if action == "close_client":
        return app.close_client(client)
    if action == "services":
        caps = app.provider.capabilities()
        return {
            "services": [
                dict(
                    service=app.profile["service"],
                    name="Disposable session qualification",
                    capabilities=caps,
                    trusted_provider=caps["provider"],
                    receiver_pin=app.receiver_pin,
                    mode="disposable",
                )
            ]
        }
    if action == "prepare":
        return app.prepare(client, args)
    if action == "update_expiry":
        return app._preparation(
            app.provider.update_expiry(client, args["request_handle"], args["expires_at"])
        )
    if action == "owner_challenge":
        return app.provider.owner_challenge(
            client, args["request_handle"], args["owner_public_key"], args["installation_id"]
        )
    if action == "submit":
        return app.submit(client, args["request_handle"], args["owner_proof"])
    if action == "reconcile":
        return app.reconcile(client, args["request_handle"])
    return app.execute(client, args["credential_handle"], args["request_id"], args["call"])


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Private disposable session provider; no installed host access"
    )
    parser.add_argument("--profile-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        app = DisposableSessionApplication(args.profile_file)
    except Exception:
        print("disposable_session_profile_unavailable", file=sys.stderr)
        return 2
    try:
        while True:
            raw = sys.stdin.buffer.readline(MAX + 1)
            if not raw:
                return 0
            if len(raw) > MAX or not raw.endswith(b"\n"):
                print("invalid_session_provider_frame", file=sys.stderr)
                return 2
            request_id = None
            try:
                value = _strict_json_object(raw, label="private provider request")
                candidate = value.get("request_id")
                if isinstance(candidate, str) and re.fullmatch(r"[0-9a-f]{32}", candidate):
                    request_id = candidate
                result = dict(
                    schema="devgraph.session-provider-result.v1",
                    request_id=request_id,
                    ok=True,
                    value=dispatch(app, value),
                )
                if len(canonical_json(result)) > MAX:
                    raise ValueError()
            except Exception:
                # No raw Python error, filesystem path, key, signature, or
                # permission payload crosses this internal error boundary.
                result = dict(
                    schema="devgraph.session-provider-result.v1",
                    request_id=request_id,
                    ok=False,
                    error="session_provider_denied_or_outcome_unknown",
                )
            sys.stdout.buffer.write(canonical_json(result) + b"\n")
            sys.stdout.buffer.flush()
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
