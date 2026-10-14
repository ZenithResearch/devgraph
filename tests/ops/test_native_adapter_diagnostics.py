import sys

import pytest

from devgraph.ops.native_adapter_diagnostics import (
    NativeAdapterError,
    diagnostic_code,
    run_adapter,
)


@pytest.mark.parametrize("raw", [
    b'{"error":"invalid_work_request","detail":"private-marker"}',
    b'{"error":"private-marker"}',
    b'{"error":"invalid_work_request","error":"unsafe_signing_key_file"}',
    b'private-marker\n{"error":"invalid_work_request"}',
    b'{"error":["invalid_work_request"]}',
    b'\xff',
    b'[' * 500,
    b' ' * 513,
    None,
])
def test_only_exact_bounded_allowlisted_diagnostics_are_admitted(raw):
    assert diagnostic_code(raw) is None


def test_adapter_keeps_only_a_public_error_code_and_discards_stdout():
    result = run_adapter([
        sys.executable, "-c",
        "import os; os.write(1,b'private-marker'); "
        "os.write(2,b'{\"error\":\"invalid_work_request\"}\\n'); raise SystemExit(2)",
    ], env={})
    assert result.returncode == 2
    assert result.stdout is None
    assert diagnostic_code(result.stderr) == "invalid_work_request"


def test_adapter_does_not_return_unknown_private_stderr():
    result = run_adapter([
        sys.executable, "-c", "import os; os.write(2,b'private-marker'); raise SystemExit(2)",
    ], env={})
    assert result.stderr is None


def test_output_budget_stops_child_instead_of_capturing_unbounded_stderr():
    with pytest.raises(NativeAdapterError, match="native_adapter_output_invalid"):
        run_adapter([
            sys.executable, "-c",
            "import os; os.write(2,b'x'*1024); import time; time.sleep(10)",
        ], env={}, timeout=2)


@pytest.mark.parametrize("close_stderr", [False, True])
def test_timeout_is_bounded_even_when_child_closes_stderr(close_stderr):
    with pytest.raises(NativeAdapterError, match="native_adapter_timed_out"):
        run_adapter([
            sys.executable, "-c",
            f"import os,time; {'os.close(2);' if close_stderr else ''} time.sleep(10)",
        ], env={}, timeout=0.2)


def test_adapter_runs_with_only_explicit_environment(monkeypatch):
    monkeypatch.setenv("UNRELATED_SECRET", "private-marker")
    result = run_adapter([
        sys.executable, "-c",
        "import os; raise SystemExit(1 if 'UNRELATED_SECRET' in os.environ else 0)",
    ], env={})
    assert result.returncode == 0


def test_missing_binary_has_no_path_or_os_error_disclosure():
    with pytest.raises(NativeAdapterError, match="^native_adapter_unavailable$"):
        run_adapter(["/missing/private-marker"], env={})
