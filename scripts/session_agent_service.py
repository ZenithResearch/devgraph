#!/usr/bin/env python3
"""Pinned source-only launcher for an explicitly configured private provider."""

from pathlib import Path

source_root = Path(__file__).resolve().parents[1] / "src"
bootstrap = source_root / "devgraph/_source_bootstrap.py"
namespace = {"__name__": "_devgraph_source_bootstrap"}
exec(compile(bootstrap.read_bytes(), str(bootstrap), "exec"), namespace)
namespace["activate"](source_root)
from devgraph.session_agent_service import main  # noqa: E402

raise SystemExit(main())
