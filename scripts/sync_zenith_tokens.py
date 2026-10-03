"""Vendor the exact, committed Zenith UI token file; verify without a sibling checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/devgraph/frontend/static/topology/zenith-tokens.css"
MANIFEST = ROOT / "docs/dev/zenith-tokens.json"
SOURCE_PATH = "public/tokens.css"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Local Zenith UI checkout")
    parser.add_argument("--update", action="store_true", help="Copy tokens and update the pin")
    args = parser.parse_args()
    if args.update and args.source is None:
        parser.error("--update requires --source")
    if args.source:
        source = args.source.resolve()
        data = (source / SOURCE_PATH).read_bytes()
        committed = subprocess.check_output(
            ["git", "-C", str(source), "show", f"HEAD:{SOURCE_PATH}"]
        )
        if data != committed:
            raise SystemExit("Commit the upstream token changes before importing them.")
        revision = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
        expected = {
            "source": "zenith-ui",
            "path": SOURCE_PATH,
            "revision": revision,
            "sha256": hashlib.sha256(data).hexdigest(),
            "themes": list(dict.fromkeys(re.findall(r'\[data-theme="([a-z]+)"\]', data.decode()))),
        }
        if args.update:
            TARGET.write_bytes(data)
            MANIFEST.write_text(json.dumps(expected, indent=2) + "\n")
        elif TARGET.read_bytes() != data or json.loads(MANIFEST.read_text()) != expected:
            raise SystemExit("Token source differs from the pin; review before running --update.")
    manifest = json.loads(MANIFEST.read_text())
    if hashlib.sha256(TARGET.read_bytes()).hexdigest() != manifest["sha256"]:
        raise SystemExit("Vendored tokens differ from the recorded upstream bytes.")
    print(f"Zenith tokens verified: {manifest['revision']} ({', '.join(manifest['themes'])})")


if __name__ == "__main__":
    main()
