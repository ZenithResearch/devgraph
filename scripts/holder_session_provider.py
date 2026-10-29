#!/usr/bin/env python3
"""Pinned source launcher; never opens an HTTP listener or selects a profile."""

from pathlib import Path

source_root = Path(__file__).resolve().parents[1] / "src"
bootstrap = source_root / "devgraph/_source_bootstrap.py"
namespace = {"__name__": "_devgraph_source_bootstrap"}
exec(compile(bootstrap.read_bytes(), str(bootstrap), "exec"), namespace)
namespace["activate"](source_root)
from devgraph.holder_session_provider import main  # noqa: E402

raise SystemExit(main())
