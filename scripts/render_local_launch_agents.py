#!/usr/bin/env python3
"""Compatibility wrapper for package-owned local launch-agent rendering."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from devgraph.local_host import (
    DEFAULT_HOST_ROOT,
    DEFAULT_LAUNCH_AGENT_ROOT,
    DEFAULT_LOG_ROOT,
    LEGACY_DATA_ROOT,
    LocalHostConfig,
    RenderError,
)
from devgraph.local_host import (
    install_agents as install_configured_agents,
)
from devgraph.local_host import (
    render_agents as render_configured_agents,
)

DEFAULT_DATA_ROOT = LEGACY_DATA_ROOT
DEFAULT_OUTPUT_ROOT = DEFAULT_LAUNCH_AGENT_ROOT


def _legacy_config(host_root: Path, data_root: Path, log_root: Path) -> LocalHostConfig:
    return LocalHostConfig.build(
        data_root=data_root,
        host_root=host_root,
        log_root=log_root,
        python_executable=host_root / "runtime" / "python" / "venv" / "bin" / "python",
        validate_data_root=False,
    )


def render_agents(host_root: Path, data_root: Path, log_root: Path) -> dict[str, bytes]:
    """Preserve the original renderer API over the package implementation."""

    return render_configured_agents(_legacy_config(host_root, data_root, log_root))


def install_agents(output_root: Path, agents: dict[str, bytes], *, replace: bool = False) -> None:
    config = _legacy_config(DEFAULT_HOST_ROOT, DEFAULT_DATA_ROOT, DEFAULT_LOG_ROOT)
    install_configured_agents(
        replace(config, launch_agent_root=output_root.resolve(strict=False)),
        agents,
        replace=replace,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install local Devgraph launch agents")
    parser.add_argument("--host-root", type=Path, default=DEFAULT_HOST_ROOT)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--log-root", type=Path, default=DEFAULT_LOG_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--replace", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = replace(
            _legacy_config(args.host_root, args.data_root, args.log_root),
            launch_agent_root=args.output_root.resolve(strict=False),
        )
        args.log_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        install_configured_agents(
            config,
            render_configured_agents(config),
            replace=args.replace,
        )
    except RenderError as error:
        print(f"launch agent installation failed: {error}")
        return 2
    print(f"local launch agents ready: {args.output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
