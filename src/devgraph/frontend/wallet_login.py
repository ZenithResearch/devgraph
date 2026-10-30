"""Wallet-only monitor shell composition; private/demo connections stay compatible."""

import re
import secrets

SESSION_CONNECTION_HTML = """<div class="browser-session" id="wallet-read-connection">
<strong data-session-status role="status" aria-live="polite">Checking session…</strong>
<span data-session-expiry></span>
<button type="button" data-session-retry hidden>Reconnect</button>
<button type="button" data-session-logout disabled>Sign out</button>
</div>"""


def session_shell(raw: str) -> str:
    from devgraph.frontend.wallet import WALLET_CONNECTION_HTML

    return (
        raw.replace(WALLET_CONNECTION_HTML, SESSION_CONNECTION_HTML)
        .replace(
            "/monitor/topology-assets/wallet-access.js",
            "/monitor/topology-assets/session-access.js",
        )
        .replace(
            "/monitor/topology-assets/wallet-access.css",
            "/monitor/topology-assets/session-access.css",
        )
        .replace(
            '<details class="connection-settings" id="connection-settings" open>'
            '<summary id="connection-summary">Connect to your graph with Wallet</summary>',
            '<section class="connection-settings" id="connection-settings">'
            '<span id="connection-summary" hidden></span>',
        )
        .replace(
            '</div></details>\n      <p class="status-line"',
            '</div></section>\n      <p class="status-line"',
        )
        .replace("Connect Wallet to view shared work.", "Checking your browser session.")
    )


def secure_shell(raw: str) -> tuple[str, dict[str, str]]:
    # Existing monitor scripts are inline. Per-response nonces let us protect
    # persistent child custody without enabling arbitrary inline script/eval.
    nonce = secrets.token_urlsafe(24)
    raw = re.sub(r"<script\b", f'<script nonce="{nonce}"', raw)
    return raw, {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'self'; "
            f"script-src 'nonce-{nonce}' 'self' "
            "chrome-extension: moz-extension: safari-web-extension:; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; worker-src 'self'; "
            "frame-src 'none'; frame-ancestors 'none'; object-src 'none'; "
            "base-uri 'none'; form-action 'none'"
        ),
    }
