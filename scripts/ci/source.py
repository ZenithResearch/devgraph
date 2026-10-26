"""Public GitHub Actions evidence; no environment dump or installed-host claims."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    lane = sys.argv[1]
    if lane not in {"repository", "protocol", "sdk", "codeql"}:
        raise ValueError("Unknown public CI lane")
    files = [*sorted((ROOT / "scripts/ci").glob("*.sh")),
             ROOT / "scripts/ci/source.py", ROOT / ".github/workflows/source.yml"]
    print(json.dumps({
        "schema": "devgraph.ci-source.v1",
        "provider": "github-actions",
        "lane": lane,
        "source": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "job": os.environ.get("GITHUB_JOB"),
        "files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in files},
        "tools": {tool: subprocess.check_output([tool, "--version"], text=True).strip()
                  for tool in ("uv", "node", "cargo", "rustc")},
        "installed_qualification": False,
    }, indent=2))


if __name__ == "__main__":
    main()
