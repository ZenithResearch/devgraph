"""Allowlisted, hash-checked Rust/WASM decoder assets for the Kanban editor."""

import hashlib
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response

from devgraph.frontend.selection import _HEADERS


def register_sdk_assets(app: FastAPI, package_dir: str | Path) -> None:
    """Snapshot an explicit built @devgraph/web package; never serve its directory."""
    root = Path(package_dir).resolve(strict=True)
    try:
        manifest = json.loads((root / "sdk-build.json").read_text())
        if manifest["schema"] != "devgraph.sdk-build.v2" or manifest["abi"] != "devgraph.web.v1":
            raise ValueError("incompatible_sdk")
        hashes = {entry["file"]: entry["sha256"] for entry in manifest["runtime_files"]}
        hashes["internal/devgraph_web_bg.wasm"] = manifest["wasm_sha256"]
        assets = {}
        for name, media in (
            ("internal/devgraph_web_factory.js", "text/javascript"),
            ("internal/devgraph_web_bg.wasm", "application/wasm"),
        ):
            path = (root / "dist" / name).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError("unsafe_sdk_path")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != hashes[name]:
                raise ValueError("sdk_hash_mismatch")
            assets[name] = (data, media)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid_devgraph_web_sdk_package") from exc

    @app.get("/assets/devgraph-sdk/{name:path}", include_in_schema=False)
    def asset(name: str):
        if name not in assets:
            raise HTTPException(404)
        data, media = assets[name]
        return Response(data, media_type=media, headers=_HEADERS)
