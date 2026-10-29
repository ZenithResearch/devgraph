#!/usr/bin/env python3
"""Prepare NEW local registration review files; never install or activate them."""

import argparse
import sys
from pathlib import Path

source_root = Path(__file__).resolve().parents[1] / "src"
bootstrap = source_root / "devgraph/_source_bootstrap.py"
namespace = {"__name__": "_devgraph_source_bootstrap"}
exec(compile(bootstrap.read_bytes(), str(bootstrap), "exec"), namespace)
namespace["activate"](source_root)
from devgraph.local_session_provider import prepare_registration  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--host-config", type=Path, required=True)
parser.add_argument("--runtime-config", type=Path, required=True)
parser.add_argument("--session-binary", type=Path, required=True)
parser.add_argument("--interpreter", type=Path, required=True)
parser.add_argument("--directory", type=Path, required=True)
parser.add_argument("--name", default="DevGraph")
args = parser.parse_args()
try:
    descriptor = prepare_registration(
        host_config=args.host_config,
        runtime_config=args.runtime_config,
        session_binary=args.session_binary,
        interpreter=args.interpreter,
        source_root=Path(__file__).resolve().parents[1],
        directory=args.directory,
        name=args.name,
    )
except Exception:
    print("local_session_registration_preparation_failed", file=sys.stderr)
    raise SystemExit(2) from None
print(descriptor)
