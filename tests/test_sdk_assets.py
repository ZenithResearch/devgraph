import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devgraph.frontend.sdk_assets import register_sdk_assets


def package(tmp_path):
    files = {
        "internal/devgraph_web_factory.js": b"export const test = true;",
        "internal/devgraph_web_bg.wasm": b"\0asm",
    }
    for name, data in files.items():
        path = tmp_path / "dist" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {
        "schema": "devgraph.sdk-build.v2",
        "abi": "devgraph.web.v1",
        "runtime_files": [
            {"file": name, "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files.items()
        ],
        "wasm_sha256": hashlib.sha256(files["internal/devgraph_web_bg.wasm"]).hexdigest(),
    }
    (tmp_path / "sdk-build.json").write_text(json.dumps(manifest))
    return files


def test_only_verified_assets_are_served_from_an_immutable_snapshot(tmp_path):
    files = package(tmp_path)
    app = FastAPI()
    register_sdk_assets(app, tmp_path)
    client = TestClient(app)
    for name, data in files.items():
        (tmp_path / "dist" / name).write_bytes(b"changed after startup")
        response = client.get("/assets/devgraph-sdk/" + name)
        assert response.content == data
        assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/assets/devgraph-sdk/sdk-build.json").status_code == 404
    assert client.get("/assets/devgraph-sdk/internal/private-key").status_code == 404


def test_tampered_or_missing_sdk_fails_closed(tmp_path):
    package(tmp_path)
    path = tmp_path / "dist/internal/devgraph_web_bg.wasm"
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="invalid_devgraph_web_sdk_package"):
        register_sdk_assets(FastAPI(), tmp_path)
    path.unlink()
    with pytest.raises(ValueError, match="invalid_devgraph_web_sdk_package"):
        register_sdk_assets(FastAPI(), tmp_path)
