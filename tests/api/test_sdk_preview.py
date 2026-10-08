"""The opt-in preview serves only matched public runtime assets."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devgraph.frontend.sdk_preview import register_sdk_preview


def test_preview_has_closed_assets_csp_and_no_store(tmp_path):
    (tmp_path / "internal").mkdir()
    (tmp_path / "index.js").write_text("export const initialize = () => {};", encoding="utf-8")
    (tmp_path / "internal/devgraph_web.js").write_text("export default () => {};", encoding="utf-8")
    (tmp_path / "internal/devgraph_web_factory.js").write_text(
        "export const createBindings = () => {};", encoding="utf-8"
    )
    (tmp_path / "internal/devgraph_web_bg.wasm").write_bytes(b"\0asm")
    (tmp_path / "private-profile.json").write_text("not a public asset", encoding="utf-8")
    app = FastAPI()
    register_sdk_preview(app, tmp_path)
    with TestClient(app) as client:
        page = client.get("/sdk-preview/")
        assert page.status_code == 200
        assert "same-key retry" in page.text
        assert page.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        wasm = client.get("/sdk-preview/pkg/internal/devgraph_web_bg.wasm")
        assert wasm.status_code == 200
        assert wasm.headers["content-type"] == "application/wasm"
        assert client.get("/sdk-preview/pkg/private-profile.json").status_code == 404
        assert client.get("/sdk-preview/pkg/../private-profile.json").status_code == 404
        assert client.get("/sdk-preview/app.js").status_code == 200


def test_preview_missing_artifact_fails_before_registering(tmp_path):
    with pytest.raises(ValueError, match="sdk_preview_artifact_missing"):
        register_sdk_preview(FastAPI(), tmp_path)
