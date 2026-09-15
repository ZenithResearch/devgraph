#!/usr/bin/env python3
"""Compatibility wrapper for local Devgraph storage provisioning."""

from __future__ import annotations

import argparse
from pathlib import Path

from devgraph.local_host import (
    LEGACY_DATA_ROOT,
    ProvisioningError,
    provision_storage,
)

DEFAULT_DATA_ROOT = LEGACY_DATA_ROOT


def provision(data_root: Path, *, require_mount: bool = True) -> tuple[str, str]:
    """Preserve the original script API over the package-owned provisioner."""

    return provision_storage(
        data_root,
        require_mounted_volume=require_mount,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare an existing directory for local Devgraph hosting"
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--allow-internal-directory", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        secret_state, config_state = provision(
            args.data_root,
            require_mount=not args.allow_internal_directory,
        )
    except ProvisioningError as error:
        print(f"local host provisioning failed: {error}")
        return 2
    print(f"local host storage ready: {args.data_root}")
    print(f"Neo4j credential state: {secret_state}; credential value was not displayed")
    print(f"Neo4j configuration state: {config_state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
