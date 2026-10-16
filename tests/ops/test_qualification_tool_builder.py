"""These are build-boundary tests, never installed signing qualification."""

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def builder(tmp_path):
    path = ROOT / "scripts/qualification/native-tools/build.py"
    spec = importlib.util.spec_from_file_location("qualification_tool_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sources = {}
    pins = {}
    for name in ("devgraph", "secs"):
        source = tmp_path / name
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        (source / "source.txt").write_text("immutable source\n")
        subprocess.run(["git", "-C", str(source), "add", "source.txt"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Qualification Test",
                "-c",
                "user.email=qualification@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "Fixture source",
            ],
            check=True,
        )
        sources[name] = source
        pins[name] = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
    module.PINS = pins
    return module, sources, tmp_path / "output"


def test_clean_sources_render_portable_manifest_without_changing_inputs(builder):
    module, sources, output = builder
    manifest = module.prepare(ROOT / "scripts/qualification/native-tools", **sources, output=output)
    text = manifest.read_text()
    assert "@DEVGRAPH_ROOT@" not in text and "@SECS_ROOT@" not in text
    assert str(sources["devgraph"] / "crates/devgraph-client-native") in text
    assert (output / "Cargo.lock").read_bytes() == (
        ROOT / "scripts/qualification/native-tools/Cargo.lock"
    ).read_bytes()
    for name, path in sources.items():
        module.checked_source(path, module.PINS[name])


@pytest.mark.parametrize("change", ["tracked", "untracked", "revision"])
def test_changed_source_rejected_before_creating_output(builder, change):
    module, sources, output = builder
    if change == "revision":
        module.PINS["secs"] = "0" * 40
    else:
        name = "source.txt" if change == "tracked" else "unexpected.rs"
        (sources["secs"] / name).write_text("changed\n")
    with pytest.raises(ValueError, match="exact runtime source"):
        module.prepare(ROOT / "scripts/qualification/native-tools", **sources, output=output)
    assert not output.exists()


def test_existing_build_is_not_overwritten(builder):
    module, sources, output = builder
    output.mkdir()
    (output / "keep").write_text("prior build")
    with pytest.raises(ValueError, match="fresh build"):
        module.prepare(ROOT / "scripts/qualification/native-tools", **sources, output=output)
    assert (output / "keep").read_text() == "prior build"


def test_failed_check_cannot_publish_tool_artifacts(builder, tmp_path, monkeypatch):
    module, sources, output = builder
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    cargo = fake_bin / "cargo"
    cargo.write_text("#!/bin/sh\nexit 17\n")
    cargo.chmod(0o700)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    with pytest.raises(subprocess.CalledProcessError) as failure:
        module.build(ROOT / "scripts/qualification/native-tools", **sources, output=output)
    assert failure.value.returncode == 17
    assert not (output / "artifacts").exists()
    assert not (output / "tool-provenance.json").exists()
