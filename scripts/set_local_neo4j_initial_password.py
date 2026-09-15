#!/usr/bin/env python3
"""Set Neo4j's initial password without emitting the credential."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

DEFAULT_DATA_ROOT = Path("/Volumes/Devgraph-Data")
DEFAULT_HOST_ROOT = (
    Path.home() / "Library" / "Application Support" / "Zenith" / "Devgraph"
)
DEFAULT_NEO4J_HOME = DEFAULT_HOST_ROOT / "runtime" / "neo4j" / "neo4j-community-5.26.29"
DEFAULT_JAVA_HOME = Path("/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Set the local Neo4j initial password from the external secret file"
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--neo4j-home", type=Path, default=DEFAULT_NEO4J_HOME)
    parser.add_argument("--java-home", type=Path, default=DEFAULT_JAVA_HOME)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    secret_path = args.data_root / "secrets" / "neo4j_password"
    admin = args.neo4j_home / "bin" / "neo4j-admin"
    config = args.data_root / "neo4j" / "conf"

    if not secret_path.is_file() or not admin.is_file() or not config.is_dir():
        print("initial password setup failed: required local host artifact is missing")
        return 2
    password = secret_path.read_text(encoding="utf-8").strip()
    if not password:
        print("initial password setup failed: credential file is empty")
        return 2

    environment = os.environ.copy()
    environment["JAVA_HOME"] = str(args.java_home)
    environment["NEO4J_CONF"] = str(config)
    completed = subprocess.run(
        [
            str(admin),
            "dbms",
            "set-initial-password",
            "--require-password-change=false",
            password,
        ],
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        print("initial password setup failed; credential value was not displayed")
        return completed.returncode
    print("initial Neo4j credential installed; credential value was not displayed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
