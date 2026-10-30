"""Setup-only Wallet monitor, with no graph, authority, credentials or write API.

Serve an immutable exported UI over a loopback listener behind its exact HTTPS
origin. Once reviewed endpoint authority exists, deploy endpoint_read_runtime.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from devgraph.frontend.wallet_login import secure_shell


def create_app():
    directory = Path(os.environ["DEVGRAPH_WALLET_MONITOR_SHELL"])
    origin = os.environ["DEVGRAPH_WALLET_MONITOR_ORIGIN"]
    url = urlsplit(origin)
    if (
        not directory.is_absolute()
        or directory.is_symlink()
        or not (directory / "index.html").is_file()
        or url.scheme != "https"
        or not url.netloc
        or url.username
        or url.password
        or origin != "https://" + url.netloc
    ):
        raise ValueError("explicit static shell and HTTPS origin required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.headers.getlist("host") != [url.netloc]:
            response = JSONResponse({"error": "invalid_receiver"}, status_code=421)
        elif request.method not in ("GET", "HEAD"):
            response = JSONResponse({"error": "endpoint_wallet_setup_required"}, status_code=404)
        else:
            response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
            }
        )
        response.headers.setdefault(
            "Content-Security-Policy", "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        )
        return response

    @app.get("/monitor/wallet-access/v1")
    def configuration():
        return JSONResponse({"error": "endpoint_wallet_setup_required"}, status_code=503)

    @app.get("/live")
    def live():
        return {"live": True, "mode": "wallet-setup-only", "credential_requests_enabled": False}

    # Explicit aliases avoid HTTP redirects from a TLS-terminating loopback proxy.
    def html(path):
        body, headers = secure_shell(path.read_text())
        return HTMLResponse(body, headers=headers)

    @app.get("/")
    @app.get("/monitor")
    @app.get("/monitor/")
    def monitor():
        return html(directory / "index.html")

    @app.get("/monitor/kanban")
    @app.get("/monitor/kanban/")
    def board():
        return html(directory / "monitor/kanban/index.html")

    @app.get("/login")
    def login():
        return html(directory / "login/index.html")

    app.mount("/", StaticFiles(directory=directory, html=True))
    return app
