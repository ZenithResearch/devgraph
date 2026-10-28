#!/usr/bin/env python3
"""Create only new disposable session qualification state, never installed config."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from devgraph.disposable_sessions import create_disposable_profile  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--directory", type=Path, required=True)
parser.add_argument("--operator-public-key", required=True)
parser.add_argument("--session-binary", type=Path, required=True)
parser.add_argument("--holder-binary", type=Path, required=True)
parser.add_argument("--read-binary", type=Path, required=True)
args = parser.parse_args()
try:
    profile = create_disposable_profile(
        args.directory,
        args.operator_public_key,
        session_binary=args.session_binary,
        holder_binary=args.holder_binary,
        read_binary=args.read_binary,
    )
except Exception:
    print("disposable_profile_creation_failed", file=sys.stderr)
    raise SystemExit(2) from None
print(profile)
