"""A pinned .py inventory must not execute forged cached or native aliases."""

import importlib.machinery
import importlib.util
import py_compile
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("alias", ["forged-cache", "native-alias"])
def test_launcher_compiles_first_party_source_and_ignores_unpinned_aliases(tmp_path, alias):
    repo = Path(__file__).resolve().parents[2]
    (tmp_path / "scripts").mkdir()
    package = tmp_path / "src/devgraph"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    shutil.copyfile(
        repo / "scripts/session_agent_service.py", tmp_path / "scripts/session_agent_service.py"
    )
    shutil.copyfile(repo / "src/devgraph/_source_bootstrap.py", package / "_source_bootstrap.py")
    module = package / "session_agent_service.py"
    module.write_text('def main():\n print("source-code")\n return 0\n')
    marker = tmp_path / "untrusted-code-ran"
    if alias == "forged-cache":
        bad = tmp_path / "bad.py"
        bad.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\ndef main(): return 1\n"
        )
        cache = Path(importlib.util.cache_from_source(str(module)))
        cache.parent.mkdir()
        py_compile.compile(
            str(bad),
            cfile=str(cache),
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
        )
        raw = cache.read_bytes()
        # A normal timestamp loader would consider this matching cache valid.
        raw = (
            raw[:8]
            + struct.pack("<II", int(module.stat().st_mtime), module.stat().st_size)
            + raw[16:]
        )
        cache.write_bytes(raw)
    else:
        (
            package / ("session_agent_service" + importlib.machinery.EXTENSION_SUFFIXES[0])
        ).write_bytes(b"not-native-code")
    caches_before = set(package.rglob("*.pyc"))
    result = subprocess.run(
        [sys.executable, "-I", str(tmp_path / "scripts/session_agent_service.py")],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "source-code"
    assert not marker.exists()
    assert set(package.rglob("*.pyc")) == caches_before
