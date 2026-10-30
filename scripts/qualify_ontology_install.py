#!/usr/bin/env python3
"""Qualify an installed CLI outside its checkout, with socket access denied."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def qualify(python: Path) -> dict[str, object]:
    program = """
import json,socket,sys
from devgraph.cli import main
def denied(*args, **kwargs): raise AssertionError('offline ontology inspection attempted network')
socket.socket = denied
socket.create_connection = denied
sys.argv = ['devgraph', 'ontology', '--format', 'sources-v1']
raise SystemExit(main())
"""
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    with tempfile.TemporaryDirectory(prefix="devgraph-installed-ontology-") as directory:
        output = subprocess.check_output([str(python), "-I", "-c", program], cwd=directory,
                                         env=env, text=True)
        result = json.loads(output)
        assert result["schemaVersion"] == "devgraph.ontology-sources.v1"
        assert result["closureVerified"] is True and result["runtimeAdmission"] is False
        for key in ("adoptedCore", "extension", "runtimeCompatibility"):
            assert Path(result[key]["manifestPath"]).is_file()
            assert Path(result[key]["ontologyPath"]).is_file()
        legacy = json.loads(subprocess.check_output(
            [str(python), "-I", "-m", "devgraph.cli", "ontology"], cwd=directory,
            env=env, text=True))
        assert legacy["version"] == result["runtimeCompatibility"]["version"]
        assert "canonical_vocabulary" not in legacy and "extension" not in legacy
        return {"schema": result["schemaVersion"], "core": result["adoptedCore"]["version"],
                "extension": result["extension"]["version"], "runtime": legacy["version"],
                "offlineClosureVerified": True, "legacyContractPreserved": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    print(json.dumps(qualify(parser.parse_args().python.absolute()), sort_keys=True))
