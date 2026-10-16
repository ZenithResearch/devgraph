import json
from pathlib import Path

import pytest

from devgraph.signed_read_runtime import create_app, private_json


def test_receiver_never_enables_without_explicit_configuration(monkeypatch):
    monkeypatch.delenv("DEVGRAPH_SIGNED_READ_CONFIG", raising=False)
    with pytest.raises(RuntimeError, match="explicit reviewed configuration"):
        create_app()


def test_configuration_is_private_unique_bounded_and_not_a_symlink(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"schema": "fixture"}))
    path.chmod(0o600)
    assert private_json(path) == {"schema": "fixture"}
    path.chmod(0o644)
    with pytest.raises(ValueError):
        private_json(path)


    path.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        private_json(link)
    path.write_text('{"schema":"one","schema":"two"}')
    with pytest.raises(ValueError):
        private_json(path)


@pytest.mark.parametrize("data_root", [None, "relative/path"])
def test_live_receiver_requires_retained_audit(monkeypatch, data_root):
    monkeypatch.setenv("DEVGRAPH_SIGNED_READ_CONFIG", "/not-read-before-audit-check.json")
    monkeypatch.delenv("DEVGRAPH_DATA_ROOT", raising=False)
    if data_root is not None:
        monkeypatch.setenv("DEVGRAPH_DATA_ROOT", data_root)
    with pytest.raises(RuntimeError, match="retained audit"):
        create_app()
