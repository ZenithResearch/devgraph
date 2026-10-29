"""A stalled or corrupt native child must fail closed without a restart."""

import sys
import time

import pytest

from devgraph.disposable_sessions import NativeSessionProcess


@pytest.mark.parametrize("response", [None, "not-json\n", "{}\nextra\n", "x" * 131073])
def test_bounded_native_exchange_closes_unusable_child(tmp_path, response):
    binary = tmp_path / "fixture-child"
    code = "import sys,time\nsys.stdin.buffer.readline()\n"
    if response is not None:
        code += f"sys.stdout.buffer.write({response.encode()!r});sys.stdout.buffer.flush()\n"
    code += "time.sleep(5)\n"
    binary.write_text(f"#!{sys.executable}\n" + code)
    binary.chmod(0o700)
    process = NativeSessionProcess(
        str(binary), tmp_path / "unused", timeout_seconds=0.1 if response is None else 1
    )
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="session_custody_unavailable"):
            process.call("create_session", expires_at=int(time.time()) + 60)
        assert time.monotonic() - started < 2
        assert process.child.poll() is not None
        with pytest.raises(RuntimeError, match="session_custody_unavailable"):
            process.call("create_session", expires_at=int(time.time()) + 60)
    finally:
        process.close()


def test_malformed_json_closes_custody_without_depending_on_child_startup(tmp_path, monkeypatch):
    binary = tmp_path / "fixture-child"
    binary.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(5)\n")
    binary.chmod(0o700)
    process = NativeSessionProcess(str(binary), tmp_path / "unused")
    monkeypatch.setattr(process, "_exchange", lambda raw: b"not-json\n")
    try:
        with pytest.raises(RuntimeError, match="^session_custody_unavailable$"):
            process.call("create_session", expires_at=int(time.time()) + 60)
        assert process.child.poll() is not None
    finally:
        process.close()
