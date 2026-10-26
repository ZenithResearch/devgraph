"""An npm advisory must stop both SDK installs before their consumers run."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("failing_audit", [1, 2])
def test_sdk_audit_failure_stops_the_lane_before_the_affected_consumer(tmp_path, failing_audit):
    source = (ROOT / "scripts/ci/run.sh").read_text()
    sdk_function = source[source.index("sdk_checks() {") : source.index("\n}\n\ncase") + 2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("cargo", "npx", "node"):
        path = fake_bin / name
        path.write_text("#!/bin/bash\nexit 0\n")
        path.chmod(0o700)
    npm = fake_bin / "npm"
    npm.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "$DG_NPM_CALLS"\n'
        'for arg in "$@"; do\n'
        '  if [[ "$arg" == audit ]]; then\n'
        '    count=$(cat "$DG_AUDIT_COUNT")\n'
        '    count=$((count + 1))\n'
        '    printf "%s" "$count" > "$DG_AUDIT_COUNT"\n'
        '    if [[ "$count" == "$DG_FAILING_AUDIT" ]]; then exit 17; fi\n'
        "  fi\n"
        "done\n"
    )
    npm.chmod(0o700)
    web = tmp_path / "packages/web"
    web.mkdir(parents=True)
    version = json.loads((ROOT / "packages/web/package.json").read_text())["version"]
    (web / f"devgraph-web-{version}.tgz").touch()
    (tmp_path / "build/ci/sdk").mkdir(parents=True)
    ci_tmp = tmp_path / "ci"
    ci_tmp.mkdir()
    count = tmp_path / "audit-count"
    count.write_text("0")
    calls = tmp_path / "npm-calls"
    script = "set -euo pipefail\nroot=$PWD\nlane=sdk\n" + sdk_function + "\nsdk_checks\n"
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
            "DG_CI_TMP": str(ci_tmp),
            "DG_CI_OUTPUT_ROOT": str(tmp_path / "build/ci"),
            "DG_NPM_CALLS": str(calls),
            "DG_AUDIT_COUNT": str(count),
            "DG_FAILING_AUDIT": str(failing_audit),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 17, result.stderr
    assert int(count.read_text()) == failing_audit
    commands = calls.read_text().splitlines()
    assert commands[0] == "ci --ignore-scripts"
    if failing_audit == 1:
        assert "run build" not in commands
    else:
        assert "run build" in commands
        assert any(command.startswith("install --prefix") for command in commands)
