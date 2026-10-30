"""Login is a public shell; only the delegated session reads protected work."""

import re
from html.parser import HTMLParser
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from devgraph.api.wallet_monitor import register_wallet_monitor
from devgraph.auth.enforcement import AuditLog
from devgraph.endpoint_read_runtime import EndpointReadBoundary
from devgraph.storage.memory import MemoryGraphStorage


class ScriptTags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nonces = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.nonces.append(dict(attrs).get("nonce"))


def client():
    app = FastAPI()
    register_wallet_monitor(
        app, storage=MemoryGraphStorage(), enrollment=SimpleNamespace(), audit_log=AuditLog()
    )
    return TestClient(
        EndpointReadBoundary(app, origin="https://graph.example"), base_url="https://graph.example"
    )


def test_login_shell_is_reachable_without_wallet_and_has_one_action():
    with client() as browser:
        page = browser.get("/login?return=%2Fmonitor%23topology")
        assert page.status_code == 200
        assert page.text.count("<button ") == 1
        assert 'id="wallet-login"' in page.text
        assert 'type="password"' not in page.text
        assert "wallet-connection-steps" not in page.text
        assert page.headers["cache-control"] == "no-store"
        assert "script-src" in page.headers["content-security-policy"]
        scripts = page.headers["content-security-policy"].split("script-src")[1].split(";")[0]
        assert "unsafe-inline" not in scripts
        assert browser.get("/assets/credential-v2/login.mjs").status_code == 200
        assert browser.get("/assets/credential-v2/session-store.mjs").status_code == 200
        assert (
            browser.get("/login").headers["content-security-policy"]
            != page.headers["content-security-policy"]
        )


def test_monitor_and_board_have_compact_session_without_legacy_wizard():
    with client() as browser:
        for path in ("/monitor", "/monitor/kanban/"):
            page = browser.get(path)
            assert page.status_code == 200
            assert "data-session-logout" in page.text
            assert "wallet-connection-steps" not in page.text
            assert "session-access.js" in page.text
            assert "Paste local" not in page.text
            csp = page.headers["content-security-policy"]
            nonce = re.search(r"script-src 'nonce-([^']+)'", csp)[1]
            scripts = ScriptTags()
            scripts.feed(page.text)
            assert scripts.nonces
            assert all(value == nonce for value in scripts.nonces)
        assert browser.get("/monitor/topology-assets/session-access.js").status_code == 200
        assert browser.get("/assets/credential-v2/private-backup.json").status_code == 404
