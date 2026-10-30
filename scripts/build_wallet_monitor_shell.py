"""Export the public Wallet monitor UI while endpoint authority is not enabled.

This setup-only static shell has no API, credential store, fixture authority or
private configuration. Use the signed endpoint runtime for actual Wallet access.
The destination must be new; keep deployment records outside the served tree.
"""

import argparse
import json
import shutil
from pathlib import Path

from devgraph.frontend.app import FRONTEND_HTML
from devgraph.frontend.wallet import WALLET_CONNECTION_HTML
from devgraph.frontend.wallet_login import session_shell


def build_shell(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    frontend = Path(__file__).resolve().parents[1] / "src/devgraph/frontend/static"
    monitor = session_shell(
        FRONTEND_HTML.replace(
            '<a href="/monitor/selection">', '<a hidden href="/monitor/selection">'
        )
    )
    (destination / "index.html").write_text(monitor)
    (destination / "monitor").mkdir()
    (destination / "monitor/index.html").write_text(monitor)
    (destination / "login").mkdir()
    (destination / "login/index.html").write_text(
        (frontend / "credential-v2/login.html").read_text()
    )
    for folder, target in (
        ("topology", "monitor/topology-assets"),
        ("credential-v2", "assets/credential-v2"),
        ("kanban", "monitor/kanban-assets"),
        ("kanban", "assets/kanban"),
    ):
        directory = destination / target
        directory.mkdir(parents=True)
        for source in (frontend / folder).iterdir():
            if source.is_file() and source.suffix in {".css", ".js", ".mjs"}:
                shutil.copyfile(source, directory / source.name)
    board = (frontend / "kanban/index.html").read_text()
    start = board.index('<form id="connect"')
    end = board.index("</form>", start) + len("</form>")
    board = (
        board[:start]
        + '<section id="connect">'
        + WALLET_CONNECTION_HTML
        + ('<button id="refresh" type="button">Refresh</button></section>')
        + board[end:]
    )
    board = board.replace('<a href="/monitor/selection">', '<a hidden href="/monitor/selection">')
    board = board.replace('<div class="connection">', '<div class="connection" hidden>')
    board = board.replace(
        "</head>",
        '<link rel="stylesheet" href="/monitor/topology-assets/wallet-access.css">'
        '<script src="/monitor/topology-assets/wallet-access.js"></script></head>',
    )
    (destination / "monitor/kanban").mkdir()
    (destination / "monitor/kanban/index.html").write_text(session_shell(board))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_shell(args.output)
    print(json.dumps({"mode": "wallet-setup-only", "api_enabled": False}))


if __name__ == "__main__":
    main()
